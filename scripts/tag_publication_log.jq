[
  .[]
  | (
      if (.jsonPayload? | type) == "object" then .jsonPayload
      else (.textPayload? | fromjson?)
      end
    )
  | select(
      type == "object"
      and .format == "mfdata-tag-refresh-attempt-v1"
      and (.state == "PUBLISHED" or .state == "UNCHANGED")
    )
]
| if length == 1 then .[0]
  else error("expected one exact publication result")
  end
