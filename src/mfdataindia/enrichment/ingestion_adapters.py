"""Discovery and preparation around the existing six fixed source adapters.

No source URL, SQL, target or method is accepted from an HTTP/environment flag.
Only existing issuer transports fetch bytes; their DNS, MIME, redirect and byte
bounds remain in force. Preparation captures the real publisher invocation,
without invoking it or appending a failure receipt before the shared claim.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from threading import RLock

from mfdataindia.enrichment import (
    ingestion_discovery as discovery, ingestion_runner as runner,
    book_sources, book_store, book_runtime, refresh_acquisition,
    refresh_runtime, refresh_store, monthly_facts, monthly_store,
    sbi_acquisition, sbi_book, sbi_runtime, kotak_acquisition,
    kotak_book_v2, kotak_runtime, uti_acquisition, uti_runtime,
    multi_runtime, multi_store, multi_facts, hdfc_discovery,
)
from mfdataindia.enrichment import store as evidence
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime


class Adapter:
    def __init__(self, source, execution, dsn, store, *, now=None, transport=None,
                 extractor=None, archive=None):
        if source not in discovery.SOURCES:
            raise ValueError('INGESTION_SOURCE_UNKNOWN')
        self.source, self.execution, self.dsn, self.store = source, execution, dsn, store
        self.observed = now or datetime.now(timezone.utc)
        self.injected_transport = transport is not None
        self.transport = transport or {
            'absl-large-cap': refresh_acquisition.fetch,
            'absl-expanded': refresh_acquisition.fetch,
            'sbi-expanded': sbi_acquisition.fetch,
            'kotak-expanded': kotak_acquisition.fetch,
            'uti-expanded': uti_acquisition.fetch,
            'hdfc-flexi-cap': hdfc_discovery.fetch,
        }[source]
        self.extractor, self.archive = extractor, archive
        self.originals, self.requests = {}, {}
        self.roles = {}
        self.lock = RLock()
        self.deadline = None

    @contextmanager
    def selected(self):
        with book_sources.select_registry(7):
            yield

    def fetch(self, url, **kwargs):
        self.deadline()
        with self.lock:
            if url in self.originals:
                if self.requests[url] != kwargs:
                    raise ValueError('INGESTION_REQUEST_REPLAY_DIFFERS')
                return self.originals[url]
        if self.source == 'absl-expanded' and url == self.spec['identity_documents'][0]['url'] and not self.injected_transport:
            value = book_runtime.fetch_identity(self.spec)
        elif self.source == 'hdfc-flexi-cap' and url == multi_facts.source()['identity_source']['url'] and not self.injected_transport:
            value = multi_runtime.fetch(url)
        else:
            value = self.transport(url, **kwargs)
        self.deadline()
        if (type(value) is not dict or value.get('source_url') != url
                or type(value.get('content')) is not bytes or not value['content']):
            raise ValueError('INGESTION_ORIGINAL_RESPONSE_INVALID')
        with self.lock:
            if url in self.originals and self.originals[url] != value:
                raise ValueError('INGESTION_CONCURRENT_ORIGINAL_CHANGED')
            self.originals[url], self.requests[url] = value, kwargs
            if sum(len(o['content']) for o in self.originals.values()) > self.maximum:
                raise ValueError('INGESTION_SOURCE_AGGREGATE_EXCEEDED')
        return value

    def _documents(self):
        return [{'role': role,'content': self.originals[url]['content']}
                for url in sorted(self.roles) if url in self.originals
                for role in sorted(self.roles[url])]

    def _inputs(self):
        return tuple({'source_url': url, 'request': self.requests[url],
                      'role':role,'evidence_class':'input' if url in self.roles else 'discovery',
                      'sha256': hashlib.sha256(self.originals[url]['content']).hexdigest()}
                     for url in sorted(self.originals)
                     for role in sorted(self.roles.get(url,{'url_'+hashlib.sha256(url.encode()).hexdigest()[:40]})))

    def _identity_roles(self):
        """Fresh index links, not previously used XML URLs, select identity inputs."""
        import re
        for url,original in list(self.originals.items()):
            match = re.fullmatch(r'https://www\.amfiindia\.com/api/schemes/([1-9][0-9]{0,7})/documents',url)
            if match:
                selected = sbi_acquisition.identity_url(original['content'],match[1])
                if selected in self.originals:
                    self.roles.setdefault(selected,set()).add('identity_'+match[1])

    def _scope(self):
        from mfdataindia.enrichment import ingestion_install
        with self.store.connect() as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY')
                conn.execute("SET LOCAL statement_timeout='30s'")
                conn.execute("SET LOCAL transaction_timeout='60s'")
                verify_runtime(conn, 'generator')
                ingestion_install.verify(conn)
                if self.source == 'absl-large-cap':
                    refresh_store.verify_acl(conn)
                    identities = [evidence.identity(conn, c) for c in sorted(monthly_facts.COHORT)]
                    identities.append(evidence.get_document(conn, refresh_runtime.IDENTITY_DOCUMENT))
                    modules = (refresh_runtime, monthly_facts, monthly_store)
                    self.maximum, self.spec = 32*1024*1024, None
                elif self.source == 'hdfc-flexi-cap':
                    multi_store.verify_acl(conn)
                    identities = [evidence.identity(conn, 101763)]
                    if identities[0] != multi_facts.source()['identity']:
                        raise ValueError('INGESTION_HDFC_IDENTITY_DIFFERS')
                    modules = (multi_runtime, multi_facts, hdfc_discovery)
                    self.maximum, self.spec = 16*1024*1024, None
                else:
                    book_store.verify_acl(conn)
                    self.spec = book_sources.specification(self.source)
                    if book_store.installed_configuration(conn)['document']['source_specifications'].get(self.source) != self.spec:
                        raise ValueError('INGESTION_INSTALLED_SOURCE_POLICY_DIFFERS')
                    identities = book_store.population(conn, self.source)
                    module = {'absl-expanded': book_runtime, 'sbi-expanded': sbi_runtime,
                              'kotak-expanded': kotak_runtime, 'uti-expanded': uti_runtime}[self.source]
                    modules = (module, kotak_book_v2) if self.source == 'kotak-expanded' else (module,)
                    self.maximum = self.spec.get('maximum_total_original_bytes', 80*1024*1024)
        self.population_hash = fingerprint(identities)
        self.derivation_version = fingerprint({'source': self.source, 'specification': self.spec,
            'engines': {m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules}})

    def discover(self, deadline):
        self.deadline = deadline
        with self.selected():
            self._scope()
            if self.source == 'uti-expanded':
                files = []
                for n,request in enumerate(uti_acquisition.recent_catalogues(self.observed)):
                    raw = self.fetch(request['source_url'])
                    found = discovery.uti_catalogue(raw['content'], raw['source_url'])
                    if found.state != discovery.State.READY:
                        return found, None
                    for kind,url in found.files:
                        self.roles[url]={'current_'+kind if n==0 else 'previous_'+kind}
                        files.append(url)
                for url in files:
                    self.fetch(url)
            elif self.source == 'kotak-expanded':
                for n,request in enumerate(kotak_acquisition.recent_catalogues(self.observed)):
                    raw = self.fetch(request['source_url'])
                    found = discovery.kotak_catalogue(raw['content'], raw['source_url'])
                    if found.state != discovery.State.READY:
                        return found, None
                    url=found.files[0][1]
                    self.roles[url]={'current_book' if n==0 else 'previous_book'}
                    self.fetch(url)
            elif self.source == 'sbi-expanded':
                raw = self.fetch(sbi_book.CATALOGUE)
                found = discovery.sbi_catalogue(raw['content'], self.observed)
                if found.state != discovery.State.READY:
                    return found, None
                for role,url in found.files:
                    self.roles[url]={role}
                    self.fetch(url)
            elif self.source in ('absl-large-cap', 'absl-expanded'):
                found = discovery.absl_catalogues(self.source,self.observed,self.fetch,
                    periods=1 if self.source=='absl-large-cap' else 2)
                if found.state != discovery.State.READY:
                    return found, None
                for role,url in found.files:
                    self.roles[url]={role}
                    self.fetch(url,book=True)
            else:
                page = self.fetch(hdfc_discovery.LISTING)
                found = hdfc_discovery.discover(page['content'], self.observed)
                if found.state != discovery.State.READY:
                    return found, None
                original = self.fetch(found.files[0][1])
                self.roles[original['source_url']]={'current_book'}
                pinned = multi_facts.source()['edition']
                if (original['source_url'] != pinned['url'] or
                        hashlib.sha256(original['content']).hexdigest() != pinned['sha256']):
                    return discovery.Discovery(self.source, discovery.State.UNSUPPORTED,
                        files=found.files, reason='NEW_EDITION_ADAPTER_REVIEW_REQUIRED'), None
                # Preserve the v1 two-request allocation. Listing + book are
                # already two requests; do not silently add a third identity
                # acquisition or reactivate the pinned publisher. The actual
                # known book may be reported unchanged only if it is published.
                if self.store.latest_published(self.source)==pinned['disclosure_as_of']:
                    return discovery.Discovery(self.source,discovery.State.UNCHANGED,
                        reason='CURRENT_INPUT_UNCHANGED',latest_period_found=pinned['disclosure_as_of']),None
                return discovery.Discovery(self.source,discovery.State.UNSUPPORTED,
                    reason='DISCOVERY_NOT_SUPPORTED'),None
            if self.source in ('sbi-expanded','kotak-expanded','uti-expanded'):
                directory=self.fetch(self.spec['directory_url'])
                self.population_hash=fingerprint({'database_population':self.population_hash,
                    'directory':sorted(sbi_book.directory(directory['content']),key=lambda r:r['scheme_id'])})
        seed = self._documents()
        prior = self.store.prior_inputs(self.source,discovery.manifest(seed),self.derivation_version,self.population_hash)
        complete = False
        if prior:
            # Rehash ALL previously consumed originals, including current AMFI
            # identity sources. Mutable catalog timestamps are observations,
            # not new financial input. Fresh index links must still agree.
            saved=prior['inputs']
            for row in saved:
                if row['evidence_class']=='discovery':
                    self.fetch(row['source_url'],**row['request'])
            complete=True
            for row in saved:
                if row['evidence_class']=='input' and row['role'].startswith('identity_'):
                    sid=row['role'].removeprefix('identity_')
                    index=self.originals.get('https://www.amfiindia.com/api/schemes/'+sid+'/documents')
                    if index is None or sbi_acquisition.identity_url(index['content'],sid)!=row['source_url']:
                        complete=False
                        break
            if complete:
                for row in saved:
                    if row['evidence_class']=='input':
                        self.roles.setdefault(row['source_url'],set()).add(row['role'])
                        self.fetch(row['source_url'],**row['request'])
        documents=self._documents() if complete else seed
        candidate = runner.Candidate(documents, self.derivation_version,
                    self.population_hash, complete=complete, inputs=self._inputs())
        return discovery.Discovery(self.source, discovery.State.READY), candidate

    def prepare(self, candidate, deadline):
        self.deadline = deadline
        def capture(conn, publisher, *args, **kwargs):
            self._identity_roles()
            if self.source=='absl-expanded':
                self.roles[self.spec['identity_documents'][0]['url']]={'issuer_identity'}
            comparisons = []
            if self.source == 'absl-large-cap':
                from mfdataindia.enrichment import monthly_derivation_replay
                _, _, day = refresh_acquisition.pages(kwargs['content'])
                period = day.isoformat()
                kwargs['equivalent_replay'] = lambda writer, snapshot: monthly_derivation_replay.resolve(writer, snapshot, comparisons)
            else:
                period = max(batch['disclosure_as_of'] for batch in args[4])
            def publish(writer):
                with self.selected():
                    return publisher(writer, *args, **kwargs)
            publish.equivalent_derivations = comparisons
            return runner.Prepared(period, publish,
                                   self._documents(), self._inputs())
        options = {'now': self.observed, 'transport': self.fetch, 'publication': capture}
        if self.extractor:
            options['extractor'] = lambda raw: self._extract(raw, deadline)
        if self.archive:
            options['archive'] = self.archive
        with self.selected():
            if self.source == 'absl-large-cap':
                result = refresh_runtime.execute(self.dsn, self.execution, **options)
            elif self.source == 'absl-expanded':
                result = book_runtime.absl(self.dsn, self.execution,
                    identity_transport=lambda spec: self.fetch(spec['identity_documents'][0]['url']), **options)
            elif self.source == 'sbi-expanded':
                result = sbi_runtime.run(self.dsn, self.execution, **options)
            elif self.source == 'kotak-expanded':
                result = kotak_runtime.run(self.dsn, self.execution, method=2, **options)
            elif self.source == 'uti-expanded':
                result = uti_runtime.run(self.dsn, self.execution, **options)
            else:
                raise ValueError('INGESTION_ADAPTER_NOT_AVAILABLE')
        deadline()
        if not isinstance(result, runner.Prepared):
            raise ValueError('INGESTION_EXISTING_OPERATION_REQUIRES_RECONCILIATION')
        return result

    def _extract(self, raw, deadline):
        deadline()
        result = self.extractor(raw)
        deadline()
        return result
