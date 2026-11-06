"""Actual packaged main and isolated decoder; synthetic only, no provider/DB."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from mfdataindia.enrichment import kotak_book_v2, layout_book_pdf, kotak_current_workload


def fixture():
    writer = PdfWriter()
    page = writer.add_blank_page(width=1200, height=1800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    left = ['KOTAK SYNTHETIC TEST FUND', 'Fund Details',
            'Benchmark Name: Nifty 500 TRI (Tier 1)', 'NAV as on October 30, 2026:',
            'Monthly AUM as on 123.45', 'October 31, 2026 (Rs. in Cr.)',
            'Name of Fund Manager:', 'Mr. First Manager', 'Total Experience: 20 years',
            'Managing Since: 01-Oct-20', 'Minimum Investment', 'Expense Ratio*',
            'Base Expense Ratio as on Oct 31, 2026', 'Regular Plan: 0.50%',
            'Direct Plan: 0.20%',
            'The expense ratio disclosed above represents the Base Expense',
            'Ratio (BER)', 'Data as on 31st October, 2026 unless otherwise specified.']
    spans = [(text, 20, 1740-n*35) for n,text in enumerate(left)]
    spans += [('Fund Performance', 300, 1705), ('The risk of the scheme is Very High', 800, 1600)]
    def escaped(text):
        return text.replace('\\','\\\\').replace('(',r'\(').replace(')',r'\)')
    stream = DecodedStreamObject()
    stream.set_data('\n'.join(f'BT /F1 10 Tf {x} {y} Td ({escaped(t)}) Tj ET' for t,x,y in spans).encode())
    page.replace_contents(stream)
    output=io.BytesIO()
    writer.write(output)
    return output.getvalue()


def smoke():
    root = Path(__file__).resolve().parents[1]
    kotak_current_workload.allocation(root)
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PG','CLOUD_RUN_')) and k!='MFDATAINDIA_DSN'}
    env.update(MFDATAINDIA_DSN='host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev dbname=mfdataindia user=mfdata_verified_generator password=synthetic-only',
               DEPLOYMENT_GIT_SHA='a'*40, CLOUD_RUN_JOB='tlws-mf-official-facts-refresh-dev',
               CLOUD_RUN_EXECUTION='tlws-mf-official-facts-refresh-dev-synthetic',
               CLOUD_RUN_TASK_COUNT='1', CLOUD_RUN_TASK_INDEX='0', CLOUD_RUN_TASK_ATTEMPT='0')
    result=subprocess.run([sys.executable,str(root/'scripts/refresh_kotak_current_dev.py'),'--validate','--kotak-only'],env=env,capture_output=True,timeout=30)
    assert result.returncode==0 and not result.stderr
    assert json.loads(result.stdout)['database_and_provider_readiness']=='UNVERIFIED'
    extraction=layout_book_pdf.extract(fixture(),profile='kotak')
    details=kotak_book_v2.detail_pages(extraction)
    assert len(details)==1
    fields=kotak_book_v2.fields(details[0],'2026-10-31')
    assert fields['aum']['value']=='123.45'
    assert fields['fund_managers']['value']==['Mr. First Manager']
    assert fields['risk_level']['value']=='Very High'
    return {'packaged_kotak_current_workload':'PASS','source_requests':0,'database_connections':0,'live_acceptance':False}


if __name__=='__main__':
    print(json.dumps(smoke(),sort_keys=True))
