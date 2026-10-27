#!/usr/bin/env python3
"""Actual packaged binding only; no secrets, database or provider operations."""
import hashlib
import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace
from mfdataindia.review import broad_reader_capture_controller_v3 as capture
from mfdataindia.review import broad_reader_capture_spec_v3 as cs
from mfdataindia.review import broad_reader_transport_controller_v2 as owner
from mfdataindia.review import broad_reader_transport_spec_v2 as spec

def platform(job, git):
    return {"DEPLOYMENT_GIT_SHA":git,"CLOUD_RUN_JOB":job,"CLOUD_RUN_EXECUTION":job+"-abcde",
            "CLOUD_RUN_TASK_INDEX":"0","CLOUD_RUN_TASK_ATTEMPT":"0","CLOUD_RUN_TASK_COUNT":"1"}

def main():
    git=os.environ["DEPLOYMENT_GIT_SHA"]
    scripts=Path(__file__).resolve().parent
    cp=runpy.run_path(str(scripts/"collect_broad_reader_capture_dev_v3.py"))
    env=platform(cs.JOB,git)
    for key,path in (("PROGRAM",scripts/"collect_broad_reader_capture_dev_v3.py"),("SPEC",Path(cs.__file__)),
                     ("INVENTORY",Path(cs.frozen.__file__)),("CONTROLLER",Path(capture.__file__))):
        env["BROAD_READER_CAPTURE_"+key+"_SHA256"]=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest=SimpleNamespace(rollout_id=cs.IDENTITY,cohort=[154117],canary_code=154117,
                             git_sha=git,image="SYNTHETIC_NO_PROVENANCE_OR_AUTHORITY")
    assert cp["worker_binding"](manifest,env)["manifest_git_sha"]==git
    for key in env:
        try:
            cp["worker_binding"](manifest,{**env,key:"unexpected"})
        except ValueError:
            pass
        else:
            raise AssertionError("capture binding drift admitted")
    manifest.rollout_id="dev-reader-cap-261025b"
    try:
        cp["worker_binding"](manifest,env)
    except ValueError:
        pass
    else:
        raise AssertionError("old narrow capture identity admitted")
    wp=runpy.run_path(str(scripts/"apply_broad_reader_access_dev_v2.py"))
    manifest.rollout_id=spec.IDENTITY
    env=platform(spec.JOB,git)
    sha=hashlib.sha256(Path(owner.__file__).read_bytes()).hexdigest()
    assert wp["packaged_binding"](manifest,sha,env)["profile_sha256"]==spec.PROFILE_SHA
    for key in env:
        try:
            wp["packaged_binding"](manifest,sha,{**env,key:"unexpected"})
        except ValueError:
            pass
        else:
            raise AssertionError("write binding drift admitted")
    manifest.rollout_id="dev-reader-acl-261025a"
    try:
        wp["packaged_binding"](manifest,sha,env)
    except ValueError:
        pass
    else:
        raise AssertionError("old narrow write identity admitted")
    for worker in (cp,wp):
        try:
            worker["run_worker"]({})
        except KeyError:
            pass
        else:
            raise AssertionError("missing manifest/payload reached DSN")
    print(json.dumps({"packaged_broad_reader_capture_owner_binding":"PASS",
                      "provider_requests":0,"database_queries":0,"runtime_or_write_authority":False}),flush=True)

if __name__=="__main__":
    main()
