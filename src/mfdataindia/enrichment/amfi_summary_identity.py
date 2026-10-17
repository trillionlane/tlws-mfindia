"""Exact issuer code/ISIN pairing from the public SchemeSummary XML layout.

Undated financial fields are deliberately ignored. Names discover families;
they are never a substitute for the code, plan, option and checksum-valid ISIN.
All code claims are retained, including Direct claims that contradict Regular.
"""

import hashlib
import re

from defusedxml import ElementTree

from mfdataindia.enrichment.evidence import valid_isin


def variant(label):
    words = re.sub(r"[^a-z0-9]+", " ", label.casefold()).split()
    plans = {p for p in ("regular", "direct") if p in words}
    if len(plans) != 1 or any(w in words for w in ("institutional", "retail")):
        return None
    options = set()
    if "growth" in words:
        options.add("GROWTH")
    if "idcw" in words or "dividend" in words or "income distribution" in " ".join(words):
        options.add("IDCW")
    if len(options) != 1:
        return None
    if options == {"GROWTH"} and any(
        w in words
        for w in (
            "daily",
            "weekly",
            "fortnightly",
            "monthly",
            "quarterly",
            "annual",
            "annually",
            "yearly",
            "payout",
            "reinvestment",
            "re",
            "investment",
        )
    ):
        return None
    recognized = set(
        "regular direct plan option growth idcw dividend income distribution cum capital withdrawal of payout pay out reinvestment re investment daily weekly fortnightly monthly quarterly annually annual yearly half semi".split()
    )
    if set(words) - recognized:
        return None
    return next(iter(plans)).upper(), next(iter(options))


def _label_entries(text, value_pattern, fund_name):
    """Consume every terminal identifier, preserving exact repeated fund prefixes.

    Plan can be explicit without the literal word Plan. Unknown plan labels
    remain code claims, never usable pairings. Unconsumed text is a gap.
    """
    text = " ".join(text.split())
    entries = []
    end = 0
    for match in re.finditer(r"(?<![A-Z0-9])(?:" + value_pattern + r")(?![A-Z0-9])", text, re.I):
        label = text[end : match.start()].strip(" ,;:-")
        if label.casefold().startswith(fund_name.casefold()):
            label = label[len(fund_name) :].strip(" ,;:-")
        if not re.match(r"^(?:Regular|Direct)\b", label, re.I) or not 1 <= len(label) <= 250:
            return []
        entries.append((label, match[0].upper()))
        end = match.end()
    if text[end:].strip(" ,;"):
        return []
    return entries


def distribution(label):
    words = re.sub(r"[^a-z0-9]+", " ", label.casefold()).split()
    frequency = {
        word
        for word in words
        if word
        in {
            "daily",
            "weekly",
            "fortnightly",
            "monthly",
            "quarterly",
            "annual",
            "annually",
            "yearly",
        }
    }
    if len(frequency) > 1 or "half" in words or "semi" in words:
        return None
    frequency = next(iter(frequency), None)
    if frequency in ("annually", "yearly"):
        frequency = "annual"
    modes = set()
    if "payout" in words or "pay out" in " ".join(words):
        modes.add("payout")
    if "reinvestment" in words or "re investment" in " ".join(words):
        modes.add("reinvestment")
    if len(modes) > 1:
        return None
    return frequency, next(iter(modes), None)


def rows(content, source_url, index_hash):
    if not isinstance(content, bytes) or not 0 < len(content) <= 1048576:
        raise ValueError("bounded original SchemeSummary XML required")
    if not re.fullmatch(
        r"https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml", source_url
    ):
        raise ValueError("exact official returned XML URL required")
    if not re.fullmatch(r"[0-9a-f]{64}", index_hash):
        raise ValueError("actual retained document-index hash required")
    root = ElementTree.fromstring(
        content, forbid_dtd=True, forbid_entities=True, forbid_external=True
    )
    summaries = root.findall("SchemeSummary") if root.tag == "SchemeSummaryDocument" else []
    if len(summaries) != 1:
        return []
    summary = summaries[0]

    def field(name):
        found = summary.findall(name)
        if len(found) != 1 or not found[0].text or len(found[0].text) > 20000:
            return ""
        return found[0].text.strip()

    name, codes, isins = field("Fund_Name"), field("AMFI_Codes_To_be_phased_out"), field("ISINs")
    if not 1 <= len(name) <= 350 or not codes or not isins:
        return []
    if re.match(r"^[1-9][0-9]{4,7}\s*[-:]", codes):
        pairs = []
        for token in codes.split(";"):
            if not token.strip():
                continue
            match = re.fullmatch(r"\s*([1-9][0-9]{4,7})\s*[-:]\s*(.{1,180}?)\s*", token)
            if not match:
                return []
            pairs.append((match[2], match[1]))
    else:
        pairs = _label_entries(codes, r"[1-9][0-9]{4,7}", name)
    identifiers = _label_entries(isins, r"[A-Z]{2}[A-Z0-9]{9}[0-9]", name)
    if not 1 <= len(pairs) <= 100 or not 1 <= len(identifiers) <= 200:
        return []
    claims = [
        {
            "code": int(code),
            "label": label,
            "variant": list(variant(label)) if variant(label) else None,
            "distribution": list(distribution(label)) if distribution(label) is not None else None,
        }
        for label, code in pairs
    ]
    result = []
    checksum = hashlib.sha256(content).hexdigest()
    for claim in claims:
        if claim["variant"] is None or claim["distribution"] is None:
            continue
        for label, isin in identifiers:
            descriptor = distribution(label)
            if (
                variant(label) != tuple(claim["variant"])
                or descriptor is None
                or not valid_isin(isin)
            ):
                continue
            code_frequency, code_mode = claim["distribution"]
            isin_frequency, isin_mode = descriptor
            group_codes = sorted({c["code"] for c in claims if c["variant"] == claim["variant"]})
            group_isins = sorted(
                {i for lab, i in identifiers if variant(lab) == tuple(claim["variant"])}
            )
            if code_mode is not None and code_mode != isin_mode:
                continue
            basis = "same_labeled_frequency"
            if code_frequency != isin_frequency:
                if (
                    None not in (code_frequency, isin_frequency)
                    or len(group_codes) != 1
                    or len(group_isins) != 1
                ):
                    continue
                basis = "unique_labeled_plan_option_pair"
            # A mode-less code may represent payout and reinvestment only
            # when there is no other distinct code in this exact frequency.
            if (
                code_mode is None
                and len(
                    {
                        c["code"]
                        for c in claims
                        if c["variant"] == claim["variant"]
                        and c["distribution"] is not None
                        and c["distribution"][0] == code_frequency
                    }
                )
                != 1
            ):
                continue
            result.append(
                {
                    "fund_name": name,
                    "code": claim["code"],
                    "isin": isin,
                    "label": claim["label"],
                    "isin_label": label,
                    "code_frequency": code_frequency,
                    "isin_frequency": isin_frequency,
                    "code_mode": code_mode,
                    "isin_mode": isin_mode,
                    "pairing_basis": basis,
                    "plan_option_codes": group_codes,
                    "plan_option_isins": group_isins,
                    "code_claims": [c for c in claims if c["code"] == claim["code"]],
                    "source_hash": checksum,
                    "source_url": source_url,
                    "locator": "/SchemeSummaryDocument/SchemeSummary/AMFI_Codes_To_be_phased_out + ISINs",
                    "index_hash": index_hash,
                }
            )
    # Retain unpaired code claims across families: they can block a conflicting
    # identity but can never themselves provide publishable identity evidence.
    for claim in claims:
        if any(r["code"] == claim["code"] and r["label"] == claim["label"] for r in result):
            continue
        result.append(
            {
                "fund_name": name,
                "code": claim["code"],
                "isin": None,
                "label": claim["label"],
                "isin_label": None,
                "code_claims": [c for c in claims if c["code"] == claim["code"]],
                "pairing_basis": "unpaired_code_claim_not_identity_evidence",
                "source_hash": checksum,
                "source_url": source_url,
                "locator": "/SchemeSummaryDocument/SchemeSummary/AMFI_Codes_To_be_phased_out + ISINs",
                "index_hash": index_hash,
            }
        )
    return result


def exact_matches(identity, bridges):
    """A contradictory claim is never repaired using the retained database."""
    if identity["plan"] != "REGULAR" or identity["option"] not in ("GROWTH", "IDCW"):
        return [], "unsupported_current_identity"
    matches = [r for r in bridges if r["code"] == identity["code"]]
    if not matches:
        return [], "issuer_code_isin_bridge_missing"
    expected = [identity["plan"], identity["option"]]
    if any(
        list(variant(r["label"]) or ()) != expected
        or not r["code_claims"]
        or any(
            c["code"] != identity["code"] or c["variant"] != expected or c["distribution"] is None
            for c in r["code_claims"]
        )
        or len(
            {tuple(c["distribution"]) for c in r["code_claims"] if c["distribution"] is not None}
        )
        > 1
        for r in matches
    ):
        return [], "issuer_variant_identity_conflict"
    if len({r["fund_name"].casefold() for r in matches}) != 1:
        return [], "issuer_family_identity_conflict"
    paired = [r for r in matches if r["isin"] in identity["isins"]]
    return (paired, None) if paired else ([], "issuer_code_isin_bridge_missing")
