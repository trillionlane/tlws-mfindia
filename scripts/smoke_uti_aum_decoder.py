"""Provider-free image/worker smoke; synthetic evidence is NEVER fund coverage."""

import io
import json

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from mfdataindia.enrichment import uti_book_pdf_v2, uti_book_pdf_v3, book_sources, uti_book
from mfdataindia.review.policy import fingerprint


def fixture(*, joined):
    writer = PdfWriter()
    page = writer.add_blank_page(width=594, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica'), NameObject('/Encoding'): NameObject('/WinAnsiEncoding')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    spans = [('UTI SYNTHETIC VALIDATION FUND', 15.624, 756.5192, 18),
             ('Portfolio as on 30th September, 2026', 219.96, 708.8738, 8),
             ('Benchmark Index', 19, 650, 8), ('Validation benchmark', 19, 635, 8),
             ('Fund Manager', 19, 620, 8), ('Fund AUM/Folio as on 30th September, 2026', 19, 441, 8),
             ('Closing AUM :' if joined else 'Closing AUM', 19.624, 418.6418, 8),
             ('`', 126, 418.6018, 8), ('4,094.35', 131.384, 419.7141, 8), ('Crore', 162.992, 418.6418, 8)]
    if not joined:
        spans.append((':', 121.504, 418.6418, 8))
    commands = [f'BT /F1 {size} Tf {x} {y} Td ({text}) Tj ET' for text, x, y, size in spans]
    stream = DecodedStreamObject()
    stream.set_data('\n'.join(commands).encode())
    page.replace_contents(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def main():
    for joined in (False, True):
        raw = fixture(joined=joined)
        old, new = uti_book_pdf_v2.extract(raw), uti_book_pdf_v3.extract(raw)
        assert new['policy_hash'] == fingerprint(uti_book_pdf_v3.policy())
        assert new['engine_hash'] == book_sources.specification(uti_book.KEY)['decoder_engine_hash']
        assert new['pages'][0]['physical_aum_row']['spans'][-2]['text'] == '4,094.35'
        assert len(new['pages'][0]['physical_aum_row']['spans']) == (4 if joined else 5)
        assert {k:v for k,v in new['pages'][0].items() if k != 'physical_aum_row'} == old['pages'][0]
    print(json.dumps({'synthetic_image_worker_smoke': 'PASS', 'live_coverage': False,
                      'database_writes': 0, 'source_requests': 0}), flush=True)


if __name__ == '__main__':
    main()
