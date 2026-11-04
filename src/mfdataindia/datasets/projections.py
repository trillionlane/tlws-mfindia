"""Reuse installed business-view seals; never expose their control inputs.

The current-facts API already validates this graph against its versioned
installation configuration and known source policy. A view is not safe merely
because its name matches: compare the full definitions, owners and execution
contexts in the caller's transaction. These reads perform no policy install,
function invocation, grant, role change or long-lived verdict caching.
"""
import psycopg

from mfdataindia.enrichment import current_facts_store as current
from mfdataindia.enrichment import monthly_current, multi_facts
from mfdataindia.review.policy import fingerprint

from . import policy as p

NAMES = frozenset({'mf.current_fund_facts',
    *(f'mf_live_facts.{name}' for name in current.VIEWS),
    'mf_multi_facts.prior_field_versions','mf_book_facts.prior_field_versions'})


def approved_oids(conn):
    """Fixed-size metadata verification once per inventory, not per dataset."""
    try:
        # pg_get_viewdef qualifies names according to search_path. Installation
        # seals use the catalog/public context; the core API normally uses mf,
        # public. Pin only these metadata reads, then roll back the savepoint so
        # successful checks, early returns and errors all restore caller state.
        # Definitions still compare in full; no SQL normalization or exception
        # to control/function/owner guards is introduced.
        with conn.transaction(force_rollback=True):
            conn.execute("SELECT pg_catalog.set_config('search_path','pg_catalog, public',true)")
            rows = conn.execute("""WITH named_views AS (
                SELECT c.oid,c.relkind,c.relowner,c.reloptions,n.nspname,n.nspowner,c.relname
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE (n.nspname||'.'||c.relname)=ANY(%s))
                SELECT oid::bigint AS oid,nspname||'.'||relname AS name,
                    relkind::text AS kind,pg_get_userbyid(relowner) AS owner,
                    pg_get_userbyid(nspowner) AS schema_owner,reloptions AS options,
                    CASE WHEN relkind='v' THEN pg_get_viewdef(oid,true) END AS definition
                FROM named_views""", (sorted(NAMES),)).fetchall()
            views = {row['name']:row for row in rows}
            # Core-only installations need no projection configuration.
            if not any(name.startswith('mf_live_facts.') for name in views):
                return []
            cfg = current.configuration(conn)  # Existing known-policy/full-seal validator.
            for name in current.VIEWS:
                row = views.get('mf_live_facts.'+name)
                if (row is None or row['kind']!='v' or row['owner']!=p.OWNER
                        or row['schema_owner']!=p.OWNER or row['options']
                        or fingerprint(row['definition'])!=cfg['view_hashes'][name]):
                    return []
            if views['mf_live_facts.baseline']['definition']!=cfg['baseline_definition']:
                return []
            approved = [views['mf_live_facts.'+name]['oid'] for name in current.VIEWS]
            facade = views.get('mf.current_fund_facts')
            if (facade and facade['kind']=='v' and facade['owner']==p.OWNER
                    and facade['schema_owner']==p.OWNER and not facade['options']
                    and facade['definition'] in (cfg['baseline_definition'],
                         views['mf_live_facts.resolved']['definition'])):
                approved.append(facade['oid'])
            # Reuse the same retained prior-graph comparisons as the original
            # multi/book guards, with one bounded query for both policy versions.
            policies = {'mf_multi_facts.prior_field_versions':fingerprint(monthly_current.policy()),
                        'mf_book_facts.prior_field_versions':fingerprint(multi_facts.current_policy())}
            prior = conn.execute("""SELECT DISTINCT ON(policy_hash) policy_hash,
                view_definitions->>'field_versions' AS definition
                FROM mf_live_facts.configuration WHERE policy_hash=ANY(%s)
                ORDER BY policy_hash,generation DESC""",(sorted(policies.values()),)).fetchall()
            definitions = {row['policy_hash']:row['definition'] for row in prior}
            for name,policy in policies.items():
                row = views.get(name)
                if (row and row['kind']=='v' and row['owner']==p.OWNER
                        and row['schema_owner']==p.OWNER and not row['options']
                        and row['definition']==definitions.get(policy)):
                    approved.append(row['oid'])
            return sorted(approved)
    except (ValueError,KeyError,TypeError,psycopg.Error):
        # Failed seals cannot authorize access. The surrounding inventory can
        # still report unrelated datasets; no ingestion transaction is undone.
        return []
