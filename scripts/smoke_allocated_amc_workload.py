"""Actual packaged allocation/main and bounded PDF subprocesses; no external I/O."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject

from mfdataindia.enrichment import allocated_amc_workload as workload
from mfdataindia.enrichment import amc_book_pdf, monthly_pdf, layout_book_pdf, uti_book_pdf


def smoke():
    root = Path(__file__).resolve().parents[1]
    allocation = workload.allocation(root)
    from smoke_kotak_current_workload import smoke as current_smoke
    current_smoke()
    from smoke_amc_discovery_workload import smoke as discovery_smoke
    discovery_smoke()
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(('PG', 'CLOUD_RUN_')) and k != 'MFDATAINDIA_DSN'}
    environment.update(
        MFDATAINDIA_DSN='host=/cloudsql/' + workload.CONNECTION
            + ' dbname=mfdataindia user=mfdata_verified_generator password=synthetic-only',
        DEPLOYMENT_GIT_SHA='a' * 40, CLOUD_RUN_JOB=workload.JOB,
        CLOUD_RUN_EXECUTION=workload.JOB + '-packaged-synthetic',
        CLOUD_RUN_TASK_INDEX='5', CLOUD_RUN_TASK_COUNT='6', CLOUD_RUN_TASK_ATTEMPT='0')
    command = [sys.executable, str(root / allocation['entrypoint']), '--validate']
    result = subprocess.run(command, env=environment, capture_output=True, timeout=30)
    assert result.returncode == 0 and not result.stderr
    observed = json.loads(result.stdout)
    assert observed['status'] == 'local_validation_passed'
    assert observed['database_and_provider_readiness'] == 'UNVERIFIED'
    environment['CLOUD_RUN_TASK_ATTEMPT'] = '1'
    result = subprocess.run(command, env=environment, capture_output=True, timeout=30)
    assert result.returncode == 1
    assert json.loads(result.stdout)['source_effect'] == 'NOT_SUBMITTED'
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b'')
    page.replace_contents(stream)
    out = io.BytesIO()
    writer.write(out)
    raw = out.getvalue()
    extractions = [monthly_pdf.extract(raw, start=1, end=1), amc_book_pdf.extract(raw),
                   layout_book_pdf.extract(raw, profile='kotak'), uti_book_pdf.extract(raw)]
    assert all(r['extractor_version'] == '6.21.4' and r['document_pages'] == 1
               and r['pages'][0]['text'] == '' for r in extractions)
    try:
        amc_book_pdf.extract(b'%PDF-1.7\nnot-a-pdf')
    except ValueError:
        pass
    else:
        raise AssertionError('invalid PDF accepted')
    return {'packaged_allocated_amc_workload': 'PASS', 'allocation_id': workload.ALLOCATION_ID,
            'allocation_sha256': workload.ALLOCATION_SHA256,
            'dependency': allocation['dependency']['version'],
            'historical_decoder_semantics': True, 'provider_requests': 0,
            'database_connections': 0, 'live_acceptance': False}


if __name__ == '__main__':
    print(json.dumps(smoke(), sort_keys=True))
