"""Static authority for dynamic issuer-linked editions, not mutable per-month pins."""
import hashlib
import json
from pathlib import Path
from copy import deepcopy
from mfdataindia.enrichment import multi_facts, monthly_pdf
from mfdataindia.review.policy import fingerprint

FILE = 'config/official-facts-sources-v2-hdfc-dynamic.json'
SHA256 = 'a455b5651cb6c78371483702d699642a87f39097ab0103004ff4189598bd78b5'


def registry(root=None):
    root=Path(root) if root else (Path('/app') if (Path('/app')/FILE).is_file() else Path(__file__).resolve().parents[3])
    raw=(root/FILE).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=SHA256:
        raise ValueError('HDFC_DYNAMIC_SOURCE_AUTHORITY_DIFFERS')
    value=json.loads(raw)
    prior=multi_facts.registry(root)
    source=value['sources'][1]
    if (set(value)!=set(prior) or value['version']!=2 or value['format']!=prior['format']
            or value['job']!=prior['job'] or value['maximum_sources_per_execution']!=2
            or value['sources'][0]!=prior['sources'][0] or source['identity']!=prior['sources'][1]['identity']
            or source['identity_source']!=prior['sources'][1]['identity_source']):
        raise ValueError('HDFC_DYNAMIC_FIXED_COHORT_DIFFERS')
    from mfdataindia.enrichment import hdfc_monthly_v2
    value['sources'][1]['parser_engine_hash']=hdfc_monthly_v2.engine_hash()
    return value


def source():
    return registry()['sources'][1]


def policy():
    value=deepcopy(multi_facts.policy())
    value.update(version=2,previous_policy_hash=fingerprint(multi_facts.policy()),
        registry_hash=fingerprint(registry()),
        authority='fixed exact identity and retained identity original; issuer-linked editions require actual corroborated month-end dates, complete panels and source/extraction witnesses',
        hdfc_discovery='official listing link selects the PDF; filename is a hint only; denied requests do not fall back to a pinned edition',
        transport='public DNS pinned TLS, no proxy/cookies, redirects, decompression or retry; fixed issuer listing plus its single selected eligible PDF and pinned identity original',
        hdfc_dates='AUM and BER printed month-end must agree with both monthly footers; manager/benchmark context dates are not field-effective dates; identity document is not monthly freshness',
        bounds={**value['bounds'],'hdfc_requests':3,'hdfc_listing_bytes':1048576},
        pdf_policy_hash=fingerprint(monthly_pdf.policy()))
    return value
