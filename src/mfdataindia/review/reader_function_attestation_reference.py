"""Immutable committed declaration reference. Pure comparison, never acceptance."""
import json

MIGRATION_SHA256 = "ac679dcfaae317a195b352c08b8319d325bf43df3ea8e54d71d376ed65f059f3"
BODY_SHA256 = "06f4c87653e1aa4423f2183e9f2476c114eec06224b6d166f1d188d7d8452aac"
BODY_BYTES = 1824
EXPECTED_DECLARATION = json.loads('''{
  "language": "plpgsql",
  "language_trusted": true,
  "cost": "100",
  "rows": "0",
  "variadic_type_oid": 0,
  "support_function_oid": 0,
  "volatility": "s",
  "parallel": "u",
  "strict": false,
  "leakproof": false,
  "returns_set": false,
  "argument_count": 1,
  "argument_default_count": 0,
  "all_argument_type_oids": null,
  "argument_modes": null,
  "transform_type_oids": null,
  "body_sha256": "06f4c87653e1aa4423f2183e9f2476c114eec06224b6d166f1d188d7d8452aac",
  "body_bytes": 1824,
  "configuration_is_exact_catalog_search_path": true,
  "argument_names_is_null": false,
  "argument_names_sha256": "4ae5d9d637de6b539d46c0c0df7ad483ce462934bd4a88880fe2a2df331cb4aa",
  "argument_names_bytes": 10,
  "binary_is_null": true,
  "binary_sha256": null,
  "binary_bytes": 0,
  "sql_body_is_null": true,
  "sql_body_sha256": null,
  "sql_body_bytes": 0,
  "argument_defaults_is_null": true,
  "argument_defaults_sha256": null,
  "argument_defaults_bytes": 0,
  "configuration_is_null": false,
  "configuration_sha256": "f156afc13bf2dac90d3d3f2965f5e4e3379aaa44d4dacc6ccd902462c63232e1",
  "configuration_bytes": 26
}''')


def comparison(function):
    """All declaration fields compare, but matching never proves call safety."""
    def same(key):
        return (key in function and json.dumps(function[key], sort_keys=True, allow_nan=False)
                == json.dumps(EXPECTED_DECLARATION[key], sort_keys=True, allow_nan=False))
    return {
        "body_matches_committed_reference": all(same(k) for k in ("body_sha256", "body_bytes")),
        "settings_match_committed_reference": all(same(k) for k in EXPECTED_DECLARATION
                                                  if k.startswith("configuration_")),
        "declaration_matches_committed_reference": all(same(k) for k in EXPECTED_DECLARATION),
        "function_invoked": False,
        "permission_acceptance": False,
    }
