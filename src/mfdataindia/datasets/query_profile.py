"""Query-local sharing of the already sealed AMC-book projection graph.

This is an execution profile, not a financial policy or a view installer. Only
request-local definitions verified by projections.inspect can select it. It
does not change the stored views, grants, cursor contract or statement limit.
"""
from dataclasses import dataclass
import re

from psycopg import sql

from mfdataindia.enrichment import book_sources
from mfdataindia.review.policy import fingerprint

from . import policy as p

VERSION = 'mfdata-sealed-projection-read-v1'
TARGETS = frozenset({'mf.current_fund_facts', *('mf_live_facts.'+name for name in
    ('field_heads','selected_fields','current_values','resolved'))})
_BATCH = '_mf_dataset_batch_inputs'
_SCALARS = (
    ("b.document -> 'observations'::text", '_dataset_observations', 1),
    ("b.document ->> 'format'::text", '_dataset_format', 2),
    ("b.document ->> 'source_url'::text", '_dataset_source_url', 1),
    ("b.document ->> 'specification_hash'::text", '_dataset_specification_hash', 1),
    ("b.document ->> 'identity_content_role'::text", '_dataset_identity_content_role', 1),
)
# PostgreSQL-deparsed definitions use ordinary quoted literals/identifiers.
# Retain their exact bytes and refuse unsupported quoting rather than applying
# reference substitutions inside strings, identifiers, comments or dollar SQL.
_LEX = re.compile(r"[eE]'(?:\\.|''|[^'\\])*'|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*(?:\n|$)|/\*[\s\S]*?\*/|[A-Za-z_][A-Za-z_0-9$]*|\s+|.")


def _require(condition):
    p.require(condition, 'DATASET_QUERY_PROFILE_UNSUPPORTED')


def _code_starts(body):
    """Only unquoted SQL token boundaries can start a finite substitution."""
    _require(type(body) is str and 0 < len(body.encode()) <= 262144)
    starts = set()
    identifiers = set()
    for token in _LEX.finditer(body):
        text = token.group()
        quoted = text[0] in "'\"" or text[:2].lower()=="e'"
        _require(not text.startswith(('--','/*')) and (quoted or '$' not in text))
        if not quoted and not text.isspace():
            starts.add(token.start())
            if re.fullmatch('[A-Za-z_][A-Za-z_0-9]*', text):
                identifiers.add(text)
        elif text.startswith('"'):
            identifiers.add(text[1:-1].replace('""','"'))
    return starts, identifiers


def _replace(body, before, after, *, count):
    """Exact finite full-byte inverse; never rewrite a quoted occurrence."""
    starts, _ = _code_starts(body)
    ends = {token.end() for token in _LEX.finditer(body)}
    positions = [m.start() for m in re.finditer(re.escape(before), body)]
    _require(len(positions) == count
             and all(i in starts and i+len(before) in ends for i in positions)
             and after not in body)
    changed = body.replace(before, after)
    _require(changed.replace(after, before) == body)
    return changed


def _reference(body, qualified, bare, *, count=None):
    occurrences = body.count(qualified)
    _require(occurrences > 0 and (count is None or occurrences == count))
    total = 0
    for keyword in ('FROM ', 'JOIN '):
        before = keyword+qualified
        n = body.count(before)
        if n:
            body = _replace(body, before, keyword+bare, count=n)
            total += n
    _require(total == occurrences)
    return body


@dataclass(frozen=True)
class Source:
    prefix: sql.Composed | sql.SQL
    relation: sql.Identifier
    profile: str | None = None


def source(entry, graph=None):
    name = entry['schema']+'.'+entry['relation']
    generic = Source(sql.SQL(''), sql.Identifier(entry['schema'],entry['relation']))
    if (name not in TARGETS or graph is None
            or graph.policy_hash not in {fingerprint(doc) for doc in book_sources.current_policies()}):
        return generic
    definitions = {name: body for name,_,body in graph.views}
    _require(name in definitions)
    versions = definitions.get('mf_live_facts.field_versions','')
    _require('mf_book_facts.prior_field_versions' in definitions
             and 'mf_book_facts.batches b' in versions)
    reserved = {_BATCH, *(alias for _,alias,_ in _SCALARS)}
    cte_names = {'prior_field_versions','field_versions','field_heads',
                 'selected_fields','current_values','resolved','current_fund_facts'}
    for body in definitions.values():
        starts, identifiers = _code_starts(body)
        _require(not reserved & identifiers)
        # A retained inner CTE must not capture one of the references we are
        # moving to the shared outer graph. Fixed aliases are code-owned.
        for match in re.finditer(r'\b([A-Za-z_][A-Za-z_0-9]*)\s+AS\b',body,re.IGNORECASE):
            _require(match.start() not in starts or match[1].lower() not in cte_names)
        _require(not any('"'+name+'" AS' in body for name in cte_names))
    versions = _reference(versions,'mf_book_facts.prior_field_versions',
                          'prior_field_versions',count=2)
    # The known v1 graph has no format/identity-content-role expressions. V2
    # has both. Do not accept a partial mix, extra document extraction, or a
    # changed number of occurrences under the same execution profile.
    v2 = "b.document ->> 'format'::text" in versions
    scalars = [row for row in _SCALARS if v2 or row[1] not in
               ('_dataset_format','_dataset_identity_content_role')]
    for expression, alias, count in scalars:
        versions = _replace(versions,expression,'b.'+alias,count=count)
    _require('b.document' not in versions)
    versions = _replace(versions,'mf_book_facts.batches b',_BATCH+' b',count=1)
    batch_columns = [sql.SQL('b.{}').format(sql.Identifier(name)) for name in
        ('batch_id','source_key','disclosure_as_of','content_hash','identity_content_hash')]
    batch_columns.extend(sql.SQL('{} AS {}').format(sql.SQL(expression),sql.Identifier(alias))
                         for expression,alias,_ in scalars)
    batch = sql.SQL('SELECT {} FROM mf_book_facts.batches b').format(sql.SQL(',').join(batch_columns))
    def view(name):
        _require('mf_live_facts.'+name in definitions)
        return definitions['mf_live_facts.'+name]
    heads = _reference(view('field_heads'),'mf_live_facts.field_versions','field_versions')
    selected = _reference(view('selected_fields'),'mf_live_facts.field_versions','field_versions')
    values = _reference(view('current_values'),'mf_live_facts.selected_fields','selected_fields')
    resolved = _reference(view('resolved'),'mf_live_facts.current_values','current_values')
    facade = definitions.get('mf.current_fund_facts')
    # If the facade is still on its captured baseline, retain that exact body.
    if facade is not None and 'mf_live_facts.current_values' in facade:
        facade = _reference(facade,'mf_live_facts.current_values','current_values')
    bodies = [('prior_field_versions','MATERIALIZED',sql.SQL('SELECT * FROM mf_book_facts.prior_field_versions')),
              (_BATCH,'MATERIALIZED',batch), ('field_versions','NOT MATERIALIZED',sql.SQL(versions)),
              ('field_heads','NOT MATERIALIZED',sql.SQL(heads)),
              ('selected_fields','MATERIALIZED',sql.SQL(selected)),
              ('current_values','MATERIALIZED',sql.SQL(values)),
              ('resolved','NOT MATERIALIZED',sql.SQL(resolved))]
    if facade is not None:
        bodies.append(('current_fund_facts','NOT MATERIALIZED',sql.SQL(facade)))
    # pg_get_viewdef ends with one semicolon; stripping that terminator is not
    # normalization of its expressions, whitespace, literals or identifiers.
    ctes = []
    for alias, mode, body in bodies:
        if isinstance(body,sql.SQL):
            text = body.as_string().rstrip()
            body = sql.SQL(text.removesuffix(';'))
        ctes.append(sql.SQL('{} AS {} ({})').format(sql.Identifier(alias),sql.SQL(mode),body))
    return Source(sql.SQL('WITH {} ').format(sql.SQL(',').join(ctes)),
                  sql.Identifier(entry['relation']), VERSION)
