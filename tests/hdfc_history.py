"""Test-only full-file previous-stage verification, separate from runtime pins."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
FILE='docs/HDFC_DYNAMIC_CURRENT_SOURCE_CHANGES_20261105.json'
SHA256='a2e7b13d5252beb49eb97339976195f6640f30d06d4ad32c54bf14fcc1a7bb84'
LATEST_SHA256='788b6d05a749b60f76a1430ef440abf623ea78486aff7ea73bc2739989bdabb6'


FUND_DETAIL_BASE = 'a4a9b02ff32ed6bad0268693a9dec762575328f7'
FUND_DETAIL_SOURCE_HASHES = {
    'tests/test_core_plan_diagnostic.py': (
        '9cb6e88667ad2bd98bfcdedaa282f4aa44cb3a30e87f75ade1676c5fe4270691',
        'c85c9ba6feb7b7868174838c2df3679dec273f4de16c62ef792f285f6b5d76c0',
    ),
    'tests/test_batch_facts_scope.py': (
        '9467a0b2f087bf1d5ab62da39991a8acd7e26f97ad165aa0cdf90c3a300edcba',
        'bfd8ad50fc7abee555b36eea502f68a193d656ee40c00605bffa5820b8ce9497',
    ),
    'tests/test_ro01_current_authority.py': (
        '7bebebe95f7f2716df1d63e0725a7d15ec85627c4f34405685c7fc0913af0c1d',
        'f54c45323b9bf21d50c075f39aaef9d9aff4e66f6a1ef91d60aa4cae378d14bf',
    ),
    'src/mfdataindia/api/queries.py': (
        '1e93b6312e19ed47d04417409debefaef81a1b5b5e4f0422d83fc9f13be52edc',
        'c2043667ab261be1ae02eb9e5f32658c142e325739487582a555530f0e3f691e',
    ),
    'src/mfdataindia/review/core_access_metadata_controller.py': (
        '236b4eab93101860ba5f389525edca2ce0dcf7cdbb860a3fffcf39e7e3decbcf',
        'a22172492b6226c413f591fa1ae396b9d4e4f58bb7cb6ce7c6ae6e59a8ea0c6f',
    ),
    'src/mfdataindia/review/core_access_metadata_controller_v2.py': (
        'ba0ee874a3f8c3253110d1a9140f69f83bb679e72645eb3c28bb5d762b0ec134',
        '1f503ab216890edd8b5e2724aec8da06f03169ae4b0fc5ae4bd3391928bc9946',
    ),
    'src/mfdataindia/review/core_access_metadata_spec.py': (
        'b25ad434c993a6f464dadad161f33d4dd756b4b1dd172181b2397d8fee6cb04a',
        'fcf23dba0f58fdbb1b88cf1ec336f8b9089c282de548e99f5ee4d670478c80d0',
    ),
    'src/mfdataindia/review/core_access_metadata_spec_v2.py': (
        '242d3704b6d10fd69a196bc3165624933e97476bba15bb38d65bbeee53450b11',
        '8f0abd4d2cbe721e4cd97558c76edaf95fc6ecb66885a6a314ed508304095580',
    ),
    'src/mfdataindia/review/core_permission_controller.py': (
        'd430aa77f87592121b93752110372d603006de34cc5c30549231573194068fe7',
        '2f7901805ee3a8df2c68a397c597e50d0be58ef01cb7d7dbaf3650ca1ece2c0e',
    ),
    'src/mfdataindia/review/core_permission_spec.py': (
        'e662a7cb7266436b86ae6aec66d6bf31f69aa064900af2f04122d328f9887fe0',
        'e93e09e0a4fb133b509c06963b21bb424d157e7d103259ae85fd89bdc877eafd',
    ),
    'src/mfdataindia/review/reader_policy_source_version.py': (
        '0411ecbac216686a7b96def5cc59797077fe2a490bddc8eb19c9e8de808df52b',
        '9067edd784f70be59a30d236b98810df579da95576a8dd178bdbbfb6a9d9eae6',
    ),
}


def previous(path,data):
    # Explicit current policy-neutral changes restore whole reviewed files;
    # this is test-only historical composition, never executable authority.
    latest_raw=(ROOT/'docs/HDFC_INSTALLER_CURRENT_SOURCE_CHANGES_20261109.json').read_bytes()
    assert hashlib.sha256(latest_raw).hexdigest()==LATEST_SHA256
    latest=json.loads(latest_raw)
    name=Path(path).resolve().relative_to(ROOT).as_posix()
    # Restore only complete files from the fund-detail query attribution update.
    detail = FUND_DETAIL_SOURCE_HASHES.get(name)
    if detail is not None and hashlib.sha256(data).hexdigest() == detail[0]:
        old = subprocess.check_output(['git', 'show', FUND_DETAIL_BASE + ':' + name], cwd=ROOT)
        assert hashlib.sha256(old).hexdigest() == detail[1], name
        data = old
    row=latest['modified'].get(name)
    if row is not None and hashlib.sha256(data).hexdigest()==row['current_sha256']:
        old=subprocess.check_output(['git','show',latest['base']+':'+name],cwd=ROOT)
        assert hashlib.sha256(old).hexdigest()==row['previous_sha256']
        data=old
    raw=(ROOT/FILE).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==SHA256
    document=json.loads(raw)
    name=Path(path).resolve().relative_to(ROOT).as_posix()
    row=document['modified'].get(name)
    if row is None or hashlib.sha256(data).hexdigest()!=row['current_sha256']:
        return data
    old=subprocess.check_output(['git','show',document['base']+':'+name],cwd=ROOT)
    assert hashlib.sha256(old).hexdigest()==row['previous_sha256']
    return old
