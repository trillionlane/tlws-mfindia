"""Known web-only PR coverage. Not full/native regression admission."""
import argparse
import hashlib
import json
from pathlib import Path

import pytest

from ci_risk_policy import NATIVE_MODULES, environment_plan, verify_plan


class Targeted:
    def __init__(self):
        self.inventory = None

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items):
        all_ids = [item.nodeid for item in items]
        if not all_ids or len(all_ids) != len(set(all_ids)):
            raise pytest.UsageError('empty or duplicate CI inventory')
        native = [item for item in items if item.get_closest_marker('postgres') is not None]
        actual = {item.nodeid.split('::')[0] for item in native}
        if not NATIVE_MODULES <= actual:
            raise pytest.UsageError('missing affected native coverage')
        selected, deselected = [], []
        for item in items:
            keep = (item.get_closest_marker('postgres') is None
                    or item.nodeid.split('::')[0] in NATIVE_MODULES)
            (selected if keep else deselected).append(item)
        self.inventory = {'all': all_ids, 'selected': [i.nodeid for i in selected],
                          'deselected': [i.nodeid for i in deselected],
                          'selected_native': [i.nodeid for i in selected if i in native]}
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)
        print('\nCI targeted collection: ' + json.dumps({k: len(v) for k, v in self.inventory.items()}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if args.evidence.exists():
        parser.error('targeted evidence already exists; use a fresh path')
    if verify_plan(environment_plan())['mode'] != 'targeted':
        raise ValueError('only reviewed targeted route may select partial native coverage')
    plugin = Targeted()
    result = int(pytest.main(['tests/', '-q', '--durations=50', '--durations-min=0.1'],
                             plugins=[plugin]))
    if result == 0 and plugin.inventory is None:
        return 5
    if result == 0:
        raw = json.dumps(plugin.inventory, sort_keys=True, separators=(',', ':')).encode()
        with args.evidence.open('x') as stream:
            json.dump({'mode': 'targeted', 'full_native_acceptance': False,
                       'exit_code': result, 'inventory': plugin.inventory,
                       'inventory_sha256': hashlib.sha256(raw).hexdigest()}, stream)
        print('CI targeted receipt: ' + str(args.evidence) + ' sha256=' + hashlib.sha256(raw).hexdigest())
    return result


if __name__ == '__main__':
    raise SystemExit(main())
