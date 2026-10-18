-- Owner-only UTI method revision; v1 fields/identity/date/TER rules retained.
BEGIN;
CREATE OR REPLACE FUNCTION mf_book_facts.seal_input() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE title_evidence jsonb; spec jsonb; item jsonb; field jsonb; pair record; pop jsonb; ids jsonb; b record; n integer; BEGIN
    SELECT document->'source_specifications'->NEW.source_key INTO spec
        FROM mf_book_facts.configuration ORDER BY generation DESC LIMIT 1;
    IF spec IS NULL THEN RAISE EXCEPTION 'registered whole-AMC source required'; END IF;
    IF spec->>'adapter' IN('uti-english-monthly-book-v1','uti-english-monthly-book-v2') THEN

    IF TG_TABLE_NAME='sources' THEN
        IF length(NEW.source_url)>2048 OR NEW.source_url ~ '[^!-~]' THEN RAISE EXCEPTION 'bounded ASCII official URL required'; END IF;
        IF NOT (
            (NEW.byte_length<=1048576 AND NEW.media_type='application/json' AND NEW.source_url ~ '^https://www\.utimf\.com/api/get-fact-sheet\?year=20[0-9]{2}&month=(January|February|March|April|May|June|July|August|September|October|November|December)$')
            OR (NEW.byte_length<=1048576 AND NEW.media_type='application/json' AND NEW.source_url=spec->>'directory_url')
            OR (NEW.byte_length<=1048576 AND NEW.media_type='application/json'
                AND NEW.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources d CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN d.media_type='application/json' AND d.source_url=spec->>'directory_url'
                    THEN convert_from(d.content,'UTF8')::jsonb ELSE '[]'::jsonb END) x
                    WHERE d.source_key=NEW.source_key AND d.source_url=spec->>'directory_url'
                    AND NEW.source_url='https://www.amfiindia.com/api/schemes/'||(x->>'scheme_id')||'/documents'))
            OR (NEW.byte_length<=1048576 AND NEW.media_type='application/xml'
                AND NEW.source_url ~ '^https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml$'
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources d CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN d.media_type='application/json' AND d.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                    THEN convert_from(d.content,'UTF8')::jsonb->'data' ELSE '[]'::jsonb END) x
                    WHERE d.source_key=NEW.source_key
                    AND x->>'summaryXmlUrl'=NEW.source_url
                    AND d.source_url='https://www.amfiindia.com/api/schemes/'||(x->>'schemeId')||'/documents'))
            OR (NEW.byte_length<=16777216 AND NEW.media_type='application/pdf'
 AND substring(NEW.content FROM 1 FOR 5)=convert_to('%PDF-','UTF8')
 AND (NEW.source_url ~ '^https://d3ce1o48hc5oli\.cloudfront\.net/utimf-job/ebook/UTI%20Fund%20Watch%20\((Active|Passive)\)-(January|February|March|April|May|June|July|August|September|October|November|December)%2020[0-9]{2}\.pdf$' OR NEW.source_url ~ '^https://d3ce1o48hc5oli\.cloudfront\.net/s3fs-public/20[0-9]{2}-(0[1-9]|1[0-2])/uti_fund_watch_(active|passive)_[a-z]+_20[0-9]{2}(_rv[1-9][0-9]?)?\.pdf\?VersionId=[A-Za-z0-9._-]{1,200}$') AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent
 CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN parent.media_type='application/json'
 AND parent.source_url ~ '^https://www\.utimf\.com/api/get-fact-sheet\?year=20[0-9]{2}&month=(January|February|March|April|May|June|July|August|September|October|November|December)$' THEN convert_from(parent.content,'UTF8')::jsonb->'rows' ELSE '[]'::jsonb END) link
 WHERE parent.source_key=NEW.source_key
 AND NEW.source_url=replace(link->>'url',' ','%20')
 AND link->>'doc'=link->>'url' AND link->>'type'='pdf' AND link->>'category'='Fact Sheet'
 AND link->>'year'=substring(parent.source_url FROM 'year=(20[0-9]{2})&')
 AND link->>'month'=substring(parent.source_url FROM 'month=([A-Za-z]+)$')
 AND link->>'name'='UTI Fund Watch ('||CASE WHEN link->>'name' LIKE 'UTI Fund Watch (Active)-%' THEN 'Active' ELSE 'Passive' END||')-'||(link->>'month')||' '||(link->>'year')))
        ) IS TRUE THEN RAISE EXCEPTION 'UTI original URL/media/parent evidence differs'; END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='batches' THEN
        IF NOT (NEW.batch_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'batch_id'),'UTF8')),'hex')
            AND NEW.document->>'batch_id'=NEW.batch_id
            AND NEW.document->>'format'='official-amc-book-batch-v2'
            AND NEW.document->>'source_key'=NEW.source_key
            AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
            AND NEW.document->>'content_hash'=NEW.content_hash
            AND NEW.document->>'identity_content_hash'=NEW.identity_content_hash
            AND NEW.document->>'disclosure_as_of'=NEW.disclosure_as_of::text
            AND NEW.disclosure_as_of=(date_trunc('month',NEW.disclosure_as_of)+interval '1 month - 1 day')::date
            AND NEW.document->>'verification_status'='needs_verification'
 AND NEW.document->>'language_selected'='English'
 AND NEW.document->>'date_authority'='actual_Portfolio_as_on_not_edition_filename_or_URL'
 AND NEW.document->>'expense_basis'='TER_separate_not_BER'
            AND NEW.document->'source_rows_changed'='0'::jsonb
            AND NEW.document->'canonical_analytics_replaced'='false'::jsonb
            AND NEW.document->'whole_factsheet_semantic_coverage'='false'::jsonb
            AND NEW.document->'adjusted_idcw_analytics_enabled'='false'::jsonb
            AND NEW.document->>'decoder_policy_hash'=spec->'parser_policy'->>'decoder_policy_hash'
            AND NEW.document->>'extraction_hash' ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(NEW.document->'observations')='array'
            AND jsonb_array_length(NEW.document->'observations') BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.content_hash=NEW.content_hash
                AND s.media_type='application/pdf' AND (NEW.document->>'source_url' ~ '^https://d3ce1o48hc5oli\.cloudfront\.net/utimf-job/ebook/UTI%20Fund%20Watch%20\((Active|Passive)\)-(January|February|March|April|May|June|July|August|September|October|November|December)%2020[0-9]{2}\.pdf$' OR NEW.document->>'source_url' ~ '^https://d3ce1o48hc5oli\.cloudfront\.net/s3fs-public/20[0-9]{2}-(0[1-9]|1[0-2])/uti_fund_watch_(active|passive)_[a-z]+_20[0-9]{2}(_rv[1-9][0-9]?)?\.pdf\?VersionId=[A-Za-z0-9._-]{1,200}$'))
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'monthly_extractions') e
                WHERE e->>'role'='active_book' AND e->>'content_hash'=NEW.content_hash AND e->>'source_url'=NEW.document->>'source_url')
            AND NEW.document->>'identity_content_role'='actual_amfi_directory_json_not_pdf'
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                AND s.content_hash=NEW.identity_content_hash AND s.source_url=spec->>'directory_url' AND s.media_type='application/json')
            AND NEW.document->>'extraction_hash'=encode(sha256(convert_to(mf_review.canonical_json(NEW.document->'monthly_extractions'),'UTF8')),'hex')
            AND jsonb_typeof(NEW.document->'document_refs')='array'
            AND jsonb_array_length(NEW.document->'document_refs') BETWEEN 9 AND 512
            AND jsonb_array_length(NEW.document->'monthly_extractions')=2
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'observations') o WHERE o->'result'->>'status'='usable')
        ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC batch source/policy/seal differs'; END IF;

        FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'document_refs') LOOP
            IF NOT (item->>'role' IN('catalogue','directory','index','identity','book')
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                    AND s.content_hash=item->>'content_hash' AND s.media_type=item->>'media_type'
                    AND CASE item->>'role'
                        WHEN 'catalogue' THEN s.media_type='application/json' AND item->>'source_url' ~ '^https://www\.utimf\.com/api/get-fact-sheet\?year=20[0-9]{2}&month=(January|February|March|April|May|June|July|August|September|October|November|December)$'
                        WHEN 'directory' THEN s.media_type='application/json' AND item->>'source_url'=spec->>'directory_url'
                        WHEN 'book' THEN s.media_type='application/pdf' AND (item->>'source_url' ~ '^https://d3ce1o48hc5oli\.cloudfront\.net/utimf-job/ebook/UTI%20Fund%20Watch%20\((Active|Passive)\)-(January|February|March|April|May|June|July|August|September|October|November|December)%2020[0-9]{2}\.pdf$' OR item->>'source_url' ~ '^https://d3ce1o48hc5oli\.cloudfront\.net/s3fs-public/20[0-9]{2}-(0[1-9]|1[0-2])/uti_fund_watch_(active|passive)_[a-z]+_20[0-9]{2}(_rv[1-9][0-9]?)?\.pdf\?VersionId=[A-Za-z0-9._-]{1,200}$') AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent
 CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN parent.media_type='application/json'
 AND parent.source_url ~ '^https://www\.utimf\.com/api/get-fact-sheet\?year=20[0-9]{2}&month=(January|February|March|April|May|June|July|August|September|October|November|December)$' THEN convert_from(parent.content,'UTF8')::jsonb->'rows' ELSE '[]'::jsonb END) link
 WHERE parent.source_key=NEW.source_key
 AND item->>'source_url'=replace(link->>'url',' ','%20')
 AND link->>'doc'=link->>'url' AND link->>'type'='pdf' AND link->>'category'='Fact Sheet'
 AND link->>'year'=substring(parent.source_url FROM 'year=(20[0-9]{2})&')
 AND link->>'month'=substring(parent.source_url FROM 'month=([A-Za-z]+)$')
 AND link->>'name'='UTI Fund Watch ('||CASE WHEN link->>'name' LIKE 'UTI Fund Watch (Active)-%' THEN 'Active' ELSE 'Passive' END||')-'||(link->>'month')||' '||(link->>'year'))
                        WHEN 'index' THEN s.media_type='application/json'
                            AND item->>'source_url' ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                            AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent CROSS JOIN LATERAL jsonb_array_elements(
                                CASE WHEN parent.media_type='application/json' AND parent.source_url=spec->>'directory_url'
                                    THEN convert_from(parent.content,'UTF8')::jsonb ELSE '[]'::jsonb END) x
                                WHERE parent.source_key=NEW.source_key AND parent.source_url=spec->>'directory_url'
                                AND item->>'source_url'='https://www.amfiindia.com/api/schemes/'||(x->>'scheme_id')||'/documents')
                        WHEN 'identity' THEN s.media_type='application/xml'
                            AND item->>'source_url' ~ '^https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml$'
                            AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent CROSS JOIN LATERAL jsonb_array_elements(
                                CASE WHEN parent.media_type='application/json' AND parent.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                                    THEN convert_from(parent.content,'UTF8')::jsonb->'data' ELSE '[]'::jsonb END) x
                                WHERE parent.source_key=NEW.source_key AND x->>'summaryXmlUrl'=item->>'source_url'
                                AND parent.source_url='https://www.amfiindia.com/api/schemes/'||(x->>'schemeId')||'/documents')
                        ELSE false END)) IS TRUE THEN
                RAISE EXCEPTION 'actual original document reference required'; END IF;
        END LOOP;
        IF (SELECT count(*) FROM jsonb_array_elements(NEW.document->'monthly_extractions') x
 WHERE x->>'role' IN('active_book','passive_book') AND x->>'language'='English'
 AND x->>'decoder_policy_hash'=NEW.document->>'decoder_policy_hash'
 AND x->>'decoder_engine_hash'=spec->>'decoder_engine_hash' AND x->>'extraction_hash' ~ '^[0-9a-f]{64}$'
 AND (x->>'source_url' ~ '^https://d3ce1o48hc5oli\.cloudfront\.net/utimf-job/ebook/UTI%20Fund%20Watch%20\((Active|Passive)\)-(January|February|March|April|May|June|July|August|September|October|November|December)%2020[0-9]{2}\.pdf$' OR x->>'source_url' ~ '^https://d3ce1o48hc5oli\.cloudfront\.net/s3fs-public/20[0-9]{2}-(0[1-9]|1[0-2])/uti_fund_watch_(active|passive)_[a-z]+_20[0-9]{2}(_rv[1-9][0-9]?)?\.pdf\?VersionId=[A-Za-z0-9._-]{1,200}$')
 AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent
 CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN parent.media_type='application/json'
 AND parent.source_url ~ '^https://www\.utimf\.com/api/get-fact-sheet\?year=20[0-9]{2}&month=(January|February|March|April|May|June|July|August|September|October|November|December)$' THEN convert_from(parent.content,'UTF8')::jsonb->'rows' ELSE '[]'::jsonb END) link
 WHERE parent.source_key=NEW.source_key AND parent.content_hash=x->>'catalogue_hash'
 AND x->>'source_url'=replace(link->>'url',' ','%20') AND link->>'url'=x->>'issuer_url'
 AND link->>'doc'=link->>'url' AND link->>'type'='pdf' AND link->>'category'='Fact Sheet'
 AND link->>'year'=substring(parent.source_url FROM 'year=(20[0-9]{2})&')
 AND link->>'month'=substring(parent.source_url FROM 'month=([A-Za-z]+)$')
 AND link->>'name'='UTI Fund Watch ('||CASE x->>'role' WHEN 'active_book' THEN 'Active' WHEN 'passive_book' THEN 'Passive' END||')-'||(link->>'month')||' '||(link->>'year'))
 AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'document_refs') r
 WHERE r->>'role'='book' AND r->>'content_hash'=x->>'content_hash' AND r->>'source_url'=x->>'source_url'))<>2
 OR (SELECT count(DISTINCT x->>'role') FROM jsonb_array_elements(NEW.document->'monthly_extractions') x)<>2
 OR (SELECT count(DISTINCT x->>'content_hash') FROM jsonb_array_elements(NEW.document->'monthly_extractions') x)<>2 THEN
 RAISE EXCEPTION 'two distinct actual English active/passive extractions required'; END IF;
 IF (SELECT count(DISTINCT r->>'content_hash') FROM jsonb_array_elements(NEW.document->'document_refs') r WHERE r->>'role'='book')<>4 THEN
 RAISE EXCEPTION 'four actual English PDF originals required'; END IF;
        SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer),count(DISTINCT x->'identity'->>'code')
            INTO pop,n FROM jsonb_array_elements(NEW.document->'observations') x;
        IF n<>jsonb_array_length(NEW.document->'observations') OR
            NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'unique population seal required'; END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'observations') LOOP
        IF spec->>'adapter'='uti-english-monthly-book-v2' AND item->'result' ? 'detail_header_evidence' THEN
            title_evidence:=item->'result'->'detail_header_evidence';
            IF NOT (jsonb_typeof(title_evidence)='object'
                AND title_evidence ?& ARRAY['method','source_hash','text','text_hash','x','y','font_points','page_width','page_height']
                AND title_evidence->>'method'='unique_original_physical_title'
                AND title_evidence->>'source_hash'=item->'result'->>'detail_content_hash'
                AND title_evidence->>'text' ~ '^UTI [A-Z0-9 &''’()/+:-]{3,200}(FUND|ETF)$'
                AND title_evidence->>'text_hash'=encode(sha256(convert_to(title_evidence->>'text','UTF8')),'hex')
                AND jsonb_typeof(title_evidence->'x')='number' AND jsonb_typeof(title_evidence->'y')='number'
                AND jsonb_typeof(title_evidence->'font_points')='number'
                AND jsonb_typeof(title_evidence->'page_width')='number' AND jsonb_typeof(title_evidence->'page_height')='number'
                AND (title_evidence->>'page_width')::numeric>0 AND (title_evidence->>'page_width')::numeric<=2000
                AND (title_evidence->>'page_height')::numeric>0 AND (title_evidence->>'page_height')::numeric<=2000
                AND (title_evidence->>'x')::numeric>=0 AND (title_evidence->>'x')::numeric<0.1*(title_evidence->>'page_width')::numeric
                AND (title_evidence->>'y')::numeric>=0.9*(title_evidence->>'page_height')::numeric
                AND (title_evidence->>'y')::numeric<=(title_evidence->>'page_height')::numeric
                AND (title_evidence->>'font_points')::numeric BETWEEN 8 AND 30
            ) IS TRUE THEN RAISE EXCEPTION 'UTI original physical-title evidence differs'; END IF;
        END IF;

            IF NOT (item->'identity'->>'amc'=spec->>'amc'
                AND (item->'identity'->>'plan'=spec->>'plan' OR (item->'result'->>'status'='gap' AND item->'result'->>'reason'='unsupported_current_identity'))
                AND item->'result'->>'status' IN('usable','gap')
 AND jsonb_typeof(item->'result'->'reported_total_expense_ratio')='object'
 AND jsonb_typeof(item->'result'->'date_discrepancies')='array'
 AND jsonb_array_length(item->'result'->'date_discrepancies')<=10
                AND (item->'result'->>'status'='usable')=EXISTS(SELECT 1 FROM jsonb_each(item->'result'->'fields') k WHERE k.value->>'status'='accepted')
                AND (item->'result'->>'status'='usable' OR length(item->'result'->>'reason')>0)
                AND (SELECT array_agg(key ORDER BY key) FROM jsonb_each(item->'result'->'fields'))=ARRAY['aum','base_expense_ratio','benchmark_name','fund_managers','risk_level']::text[]
            ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC observation shape differs'; END IF;

            IF item->'result'->>'status'='usable' OR item->'result'->'reported_total_expense_ratio'->>'status'='accepted' THEN
                IF NOT (item->'identity'->>'plan'='REGULAR' AND item->'identity'->>'option' IN('GROWTH','IDCW')
                    AND jsonb_typeof(item->'result'->'identity_evidence')='array'
                    AND jsonb_array_length(item->'result'->'identity_evidence') BETWEEN 1 AND 10
                    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(item->'result'->'identity_evidence') e WHERE NOT (
                        e->>'code'=item->'identity'->>'code' AND item->'identity'->'isins' ? (e->>'isin')
                        AND e->>'locator'='/SchemeSummaryDocument/SchemeSummary/AMFI_Codes_To_be_phased_out + ISINs'
                        AND e->>'label' ~* '^Regular([[:space:]]+Plan)?\y' AND e->>'label' !~* 'direct|institutional|retail'
                        AND e->>'isin_label' ~* '^Regular([[:space:]]+Plan)?\y' AND e->>'isin_label' !~* 'direct|institutional|retail'
                        AND CASE item->'identity'->>'option' WHEN 'GROWTH' THEN e->>'label' ~* 'growth' AND e->>'label' !~* 'idcw|dividend|income distribution'
                            ELSE e->>'label' ~* 'idcw|dividend|income distribution' AND e->>'label' !~* 'growth' END
                        AND CASE item->'identity'->>'option' WHEN 'GROWTH' THEN e->>'isin_label' ~* 'growth' AND e->>'isin_label' !~* 'idcw|dividend|income distribution'
                            ELSE e->>'isin_label' ~* 'idcw|dividend|income distribution' AND e->>'isin_label' !~* 'growth' END
                        AND e ?& ARRAY['code_frequency','isin_frequency','code_mode','isin_mode','pairing_basis']
                        AND e->'code_frequency' IN ('null'::jsonb,'"daily"'::jsonb,'"weekly"'::jsonb,'"fortnightly"'::jsonb,'"monthly"'::jsonb,'"quarterly"'::jsonb,'"annual"'::jsonb)
                        AND e->'isin_frequency' IN ('null'::jsonb,'"daily"'::jsonb,'"weekly"'::jsonb,'"fortnightly"'::jsonb,'"monthly"'::jsonb,'"quarterly"'::jsonb,'"annual"'::jsonb)
                        AND e->'code_mode' IN ('null'::jsonb,'"payout"'::jsonb,'"reinvestment"'::jsonb)
                        AND e->'isin_mode' IN ('null'::jsonb,'"payout"'::jsonb,'"reinvestment"'::jsonb)
                        AND (e->'code_mode'='null'::jsonb OR e->'code_mode'=e->'isin_mode')
                        AND jsonb_typeof(e->'plan_option_codes')='array' AND e->'plan_option_codes' @> jsonb_build_array(item->'identity'->'code')
                        AND jsonb_typeof(e->'plan_option_isins')='array' AND e->'plan_option_isins' ? (e->>'isin')
                        AND ((e->>'pairing_basis'='same_labeled_frequency' AND e->'code_frequency'=e->'isin_frequency')
                            OR (e->>'pairing_basis'='unique_labeled_plan_option_pair'
                                AND e->'code_frequency'<>e->'isin_frequency' AND 'null'::jsonb IN(e->'code_frequency',e->'isin_frequency')
                                AND jsonb_array_length(e->'plan_option_codes')=1 AND jsonb_array_length(e->'plan_option_isins')=1))
                        AND jsonb_typeof(e->'code_claims')='array' AND jsonb_array_length(e->'code_claims') BETWEEN 1 AND 100
                        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(e->'code_claims') claim WHERE
                            (claim->>'code'=item->'identity'->>'code' AND claim->'variant'=jsonb_build_array('REGULAR',item->'identity'->>'option')
                                AND claim->'distribution'=jsonb_build_array(e->'code_frequency',e->'code_mode')) IS DISTINCT FROM true)
                        AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'document_refs') r
                            WHERE r->>'role'='identity' AND r->>'media_type'='application/xml'
                            AND r->>'content_hash'=e->>'source_hash' AND r->>'source_url'=e->>'source_url')
                        AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                            AND s.content_hash=e->>'index_hash' AND s.media_type='application/json'
                            AND s.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                            AND EXISTS(SELECT 1 FROM jsonb_array_elements(convert_from(s.content,'UTF8')::jsonb->'data') d
                                WHERE d->>'summaryXmlUrl'=e->>'source_url'))
                    ) IS TRUE)
                ) IS TRUE THEN RAISE EXCEPTION 'UTI exact XML identity evidence differs'; END IF;
            END IF;
            FOR pair IN SELECT key,value FROM jsonb_each((item->'result'->'fields')||jsonb_build_object('reported_total_expense_ratio',item->'result'->'reported_total_expense_ratio')) LOOP
                field=pair.value;
                IF NOT (field->>'disclosure_as_of'=NEW.disclosure_as_of::text AND field->>'status' IN('accepted','unavailable')) IS TRUE THEN
                    RAISE EXCEPTION 'field status/date differs'; END IF;
                IF field->>'status'='unavailable' THEN
                    IF field->'value' IS DISTINCT FROM 'null'::jsonb OR COALESCE(length(field->>'reason'),0)=0 THEN
                        RAISE EXCEPTION 'unavailable field requires null and gap reason'; END IF;
                ELSE
                    IF NOT (length(field->>'source_anchor')>0 AND length(field->>'raw_value')>0
                        AND field->>'detail_page' ~ '^[1-9][0-9]{0,2}$'
                        AND field->>'detail_text_hash' ~ '^[0-9a-f]{64}$'
                        AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'monthly_extractions') e
                            WHERE e->>'content_hash'=field->>'content_hash' AND e->>'source_url'=field->>'source_url')
                        AND field->>'date_basis' IN('explicit_field_date','document_context_not_field_effective_date')
                        AND CASE WHEN field->>'date_basis'='explicit_field_date' THEN field->>'field_effective_date'=NEW.disclosure_as_of::text
                            ELSE field->'field_effective_date'='null'::jsonb END
                        AND CASE pair.key
                            WHEN 'benchmark_name' THEN field->>'unit'='name' AND field->>'scope'='fund' AND jsonb_typeof(field->'value')='string' AND length(btrim(field->>'value')) BETWEEN 1 AND 250
                                AND field->>'value' !~* '\m(ltd|limited|portfolio|NAV|AUM|regular|direct)\M'
                            WHEN 'fund_managers' THEN field->>'unit'='names' AND field->>'scope'='fund' AND CASE WHEN jsonb_typeof(field->'value')='array' THEN jsonb_array_length(field->'value') BETWEEN 1 AND 15 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(field->'value') z WHERE jsonb_typeof(z)<>'string' OR length(btrim(z#>>'{}')) NOT BETWEEN 1 AND 150) ELSE false END
                            WHEN 'base_expense_ratio' THEN false
 WHEN 'reported_total_expense_ratio' THEN field->>'expense_basis'='TER' AND field->>'unit'='percent' AND field->>'scope'='regular_plan' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,2}(\.[0-9]{1,4})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 10 ELSE false END
                            WHEN 'aum' THEN field->>'unit'='INR_crore' AND field->>'scope'='fund' AND field->>'date_basis'='explicit_field_date' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,13}(\.[0-9]{1,6})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 1000000000000 ELSE false END
                            WHEN 'risk_level' THEN field->>'unit'='risk_category' AND field->>'scope'='fund' AND field->>'value' IN('Low','Low to Moderate','Moderate','Moderately High','High','Very High')
                            ELSE false END
                    ) IS TRUE THEN RAISE EXCEPTION 'accepted field type/unit/date/evidence differs'; END IF;
                END IF;
            END LOOP;
        END LOOP;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME<>'runs' THEN RAISE EXCEPTION 'unmodelled book input'; END IF;
    IF NOT (NEW.run_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'run_id'),'UTF8')),'hex')
        AND NEW.document->>'run_id'=NEW.run_id AND NEW.document->>'format'='official-amc-book-run-v1'
        AND NEW.document->>'source_key'=NEW.source_key AND NEW.document->>'execution_id'=NEW.execution_id
        AND NEW.document->>'status'=NEW.status AND (NEW.document->>'observed_at')::timestamptz=NEW.observed_at
        AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
        AND NEW.document->'source_rows_changed'='0'::jsonb AND NEW.document->'all_amc_data_complete'='false'::jsonb
    ) IS TRUE THEN RAISE EXCEPTION 'book run seal differs'; END IF;
    IF NEW.status='failed' THEN
        IF NEW.document->'batch_ids' IS DISTINCT FROM '[]'::jsonb THEN RAISE EXCEPTION 'failed receipt cannot publish'; END IF;
        RETURN NEW;
    END IF;
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN RAISE EXCEPTION 'governed publication lock required'; END IF;
    SELECT jsonb_agg(jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,'scheme_name',f.scheme_name,
        'plan',f.plan_type,'option',f.option_type,'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
            (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
        ORDER BY f.amfi_scheme_code) INTO pop FROM mf.funds f JOIN mf.amcs a USING(amc_id)
        WHERE a.amfi_amc_name=spec->>'amc' AND f.in_scope;
    IF pop IS NULL OR jsonb_array_length(pop) NOT BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
        OR NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'complete current in-scope AMC population required'; END IF;
    SELECT jsonb_agg(batch_id ORDER BY disclosure_as_of DESC,batch_id),count(*) INTO ids,n
        FROM mf_book_facts.batches WHERE source_key=NEW.source_key AND NEW.document->'batch_ids' ? batch_id;
    IF ids IS DISTINCT FROM NEW.document->'batch_ids' OR n<>(spec->>'period_count')::integer OR
        (SELECT count(DISTINCT disclosure_as_of) FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id)<>n THEN
        RAISE EXCEPTION 'two distinct complete ordered periods required'; END IF;
    FOR b IN SELECT * FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id LOOP
        IF (SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer) FROM jsonb_array_elements(b.document->'observations') x) IS DISTINCT FROM pop
            OR b.disclosure_as_of>NEW.observed_at::date OR b.document->>'specification_hash'<>NEW.document->>'specification_hash' THEN
            RAISE EXCEPTION 'batch current identity/specification differs'; END IF;
    END LOOP;
    IF (SELECT dataset_version::text FROM mf.dataset_summary WHERE singleton) IS DISTINCT FROM NEW.document->>'dataset_version' THEN
        RAISE EXCEPTION 'run live dataset receipt differs'; END IF;
    IF NOT (jsonb_typeof(NEW.document->'availability')='array' AND jsonb_array_length(NEW.document->'availability')<=256) IS TRUE THEN
        RAISE EXCEPTION 'bounded explicit source availability required'; END IF;
    IF (SELECT jsonb_agg(disclosure_as_of::text ORDER BY disclosure_as_of DESC)
 FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id)
 IS DISTINCT FROM jsonb_build_array(
 (date_trunc('month',NEW.observed_at AT TIME ZONE 'UTC')-interval '1 day')::date::text,
 (date_trunc('month',NEW.observed_at AT TIME ZONE 'UTC')-interval '1 month 1 day')::date::text) THEN
 RAISE EXCEPTION 'two actual most recent closed reporting months required'; END IF;
    -- Catalog originals are retained byte-for-byte in the same append-only DB.
    IF NOT (jsonb_typeof(NEW.document->'catalog_hashes')='array' AND jsonb_array_length(NEW.document->'catalog_hashes') BETWEEN (spec->>'minimum_catalogue_originals')::integer AND 203
        AND (SELECT count(DISTINCT content_hash) FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.media_type IN('text/html','application/json') AND NEW.document->'catalog_hashes' ? s.content_hash)=jsonb_array_length(NEW.document->'catalog_hashes')) IS TRUE THEN
        RAISE EXCEPTION 'retained catalogue originals required'; END IF;

    SELECT count(DISTINCT r->>'content_hash') INTO n FROM mf_book_facts.batches archived_batch
        CROSS JOIN LATERAL jsonb_array_elements(archived_batch.document->'document_refs') r
        WHERE NEW.document->'batch_ids' ? archived_batch.batch_id AND r->>'media_type' IN('application/pdf','application/xml');
    IF jsonb_array_length(NEW.document->'archives') IS DISTINCT FROM n THEN RAISE EXCEPTION 'all UTI PDF/XML archives required'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'archives') LOOP
        IF NOT (item->>'generation' ~ '^[1-9][0-9]*$'
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                AND s.content_hash=item->>'sha256' AND s.byte_length=(item->>'size')::integer
                AND s.media_type IN('application/pdf','application/xml')
                AND item->>'uri'='gs://tlws_mf_data_source/'||(spec->>'archive_prefix')||s.content_hash||
                    CASE s.media_type WHEN 'application/pdf' THEN '.pdf' ELSE '.xml' END)
        ) IS TRUE THEN RAISE EXCEPTION 'UTI original archive binding differs'; END IF;
    END LOOP;
    IF (SELECT count(DISTINCT x->>'sha256') FROM jsonb_array_elements(NEW.document->'archives') x
        WHERE EXISTS(SELECT 1 FROM mf_book_facts.batches archived_batch
            CROSS JOIN LATERAL jsonb_array_elements(archived_batch.document->'document_refs') r
            WHERE NEW.document->'batch_ids' ? archived_batch.batch_id AND r->>'content_hash'=x->>'sha256'
            AND r->>'media_type' IN('application/pdf','application/xml')))<>n THEN
        RAISE EXCEPTION 'distinct complete UTI archives required'; END IF;
    IF (NEW.status='unchanged') IS DISTINCT FROM EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.source_key=NEW.source_key
        AND r.status<>'failed' AND r.document->'batch_ids'=NEW.document->'batch_ids') THEN RAISE EXCEPTION 'new versus replay status differs'; END IF;
    RETURN NEW;
    END IF;


    -- Prior source versions 1-3 remain byte-for-byte frozen below.
    IF spec->>'adapter'='kotak-monthly-book-v1' THEN

    IF TG_TABLE_NAME='sources' THEN
        IF length(NEW.source_url)>2048 OR NEW.source_url ~ '[^!-~]' THEN RAISE EXCEPTION 'bounded ASCII official URL required'; END IF;
        IF NOT (
            (NEW.byte_length<=1048576 AND NEW.media_type='application/json' AND NEW.source_url ~ '^https://www\.kotakmf\.com/api/kotakapi/forms/user/getsubheaderList/219\?year=20[0-9]{2}&month=([1-9]|1[0-2])&type=month$')
            OR (NEW.byte_length<=1048576 AND NEW.media_type='application/json' AND NEW.source_url=spec->>'directory_url')
            OR (NEW.byte_length<=1048576 AND NEW.media_type='application/json'
                AND NEW.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources d CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN d.media_type='application/json' AND d.source_url=spec->>'directory_url'
                    THEN convert_from(d.content,'UTF8')::jsonb ELSE '[]'::jsonb END) x
                    WHERE d.source_key=NEW.source_key AND d.source_url=spec->>'directory_url'
                    AND NEW.source_url='https://www.amfiindia.com/api/schemes/'||(x->>'scheme_id')||'/documents'))
            OR (NEW.byte_length<=1048576 AND NEW.media_type='application/xml'
                AND NEW.source_url ~ '^https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml$'
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources d CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN d.media_type='application/json' AND d.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                    THEN convert_from(d.content,'UTF8')::jsonb->'data' ELSE '[]'::jsonb END) x
                    WHERE d.source_key=NEW.source_key
                    AND x->>'summaryXmlUrl'=NEW.source_url
                    AND d.source_url='https://www.amfiindia.com/api/schemes/'||(x->>'schemeId')||'/documents'))
            OR (NEW.media_type='application/pdf' AND substring(NEW.content FROM 1 FOR 5)=convert_to('%PDF-','UTF8') AND (
                (NEW.source_url=spec->'reviewed_historical'->>'url' AND NEW.content_hash=spec->'reviewed_historical'->>'sha256')
                OR (NEW.source_url ~ '^https://vatseelabs-s3\.kotakmf\.com/FormsDownloads/Factsheet/Factsheet-for-[A-Za-z]+-20[0-9]{2}/KotakMFFactsheet[A-Za-z]+20[0-9]{2}\.pdf$' AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN parent.media_type='application/json' AND parent.source_url ~ '^https://www\.kotakmf\.com/api/kotakapi/forms/user/getsubheaderList/219\?year=20[0-9]{2}&month=([1-9]|1[0-2])&type=month$' THEN convert_from(parent.content,'UTF8')::jsonb->'subHeaderList' ELSE '[]'::jsonb END) link WHERE parent.source_key=NEW.source_key AND NEW.source_url='https://vatseelabs-s3.kotakmf.com/'||(link->>'content') AND link->>'headerId'='219' AND link->>'contentType'='upload'))))
        ) IS TRUE THEN RAISE EXCEPTION 'Kotak original URL/media/parent evidence differs'; END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='batches' THEN
        IF NOT (NEW.batch_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'batch_id'),'UTF8')),'hex')
            AND NEW.document->>'batch_id'=NEW.batch_id
            AND NEW.document->>'format'='official-amc-book-batch-v2'
            AND NEW.document->>'source_key'=NEW.source_key
            AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
            AND NEW.document->>'content_hash'=NEW.content_hash
            AND NEW.document->>'identity_content_hash'=NEW.identity_content_hash
            AND NEW.document->>'disclosure_as_of'=NEW.disclosure_as_of::text
            AND NEW.disclosure_as_of=(date_trunc('month',NEW.disclosure_as_of)+interval '1 month - 1 day')::date
            AND NEW.document->>'verification_status'='needs_verification'
            AND NEW.document->'source_rows_changed'='0'::jsonb
            AND NEW.document->'canonical_analytics_replaced'='false'::jsonb
            AND NEW.document->'whole_factsheet_semantic_coverage'='false'::jsonb
            AND NEW.document->'adjusted_idcw_analytics_enabled'='false'::jsonb
            AND NEW.document->>'decoder_policy_hash'=spec->'parser_policy'->>'decoder_policy_hash'
            AND NEW.document->>'extraction_hash' ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(NEW.document->'observations')='array'
            AND jsonb_array_length(NEW.document->'observations') BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.content_hash=NEW.content_hash
                AND s.media_type='application/pdf' AND (starts_with(NEW.document->>'source_url',spec->>'book_prefix') OR NEW.document->>'source_url'=spec->'reviewed_historical'->>'url'))
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'monthly_extractions') e
                WHERE e->>'role' IN('whole_book','reviewed_historical_one_pager') AND e->>'content_hash'=NEW.content_hash AND e->>'source_url'=NEW.document->>'source_url')
            AND NEW.document->>'identity_content_role'='actual_amfi_directory_json_not_pdf'
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                AND s.content_hash=NEW.identity_content_hash AND s.source_url=spec->>'directory_url' AND s.media_type='application/json')
            AND NEW.document->>'extraction_hash'=encode(sha256(convert_to(mf_review.canonical_json(NEW.document->'monthly_extractions'),'UTF8')),'hex')
            AND jsonb_typeof(NEW.document->'document_refs')='array'
            AND jsonb_array_length(NEW.document->'document_refs') BETWEEN 6 AND 512
            AND jsonb_array_length(NEW.document->'monthly_extractions')=1
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'observations') o WHERE o->'result'->>'status'='usable')
        ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC batch source/policy/seal differs'; END IF;

        field=NEW.document->'source_acquisition';
        IF field->>'mode'='reviewed_known_document_not_live_catalogue' THEN
            IF NOT (NEW.disclosure_as_of::text=spec->'reviewed_known_document_policy'->>'expected_period'
                AND NEW.content_hash=spec->'reviewed_known_document_policy'->>'pdf_sha256'
                AND field=jsonb_build_object(
                    'mode','reviewed_known_document_not_live_catalogue',
                    'policy_hash',encode(sha256(convert_to(mf_review.canonical_json(spec->'reviewed_known_document_policy'),'UTF8')),'hex'),
                    'catalogue_original_sha256',spec->'reviewed_known_document_policy'->>'catalogue_sha256',
                    'catalogue_source_url',spec->'reviewed_known_document_policy'->>'catalogue_url',
                    'expected_pdf_sha256',spec->'reviewed_known_document_policy'->>'pdf_sha256',
                    'reviewed_on',spec->'reviewed_known_document_policy'->>'reviewed_on',
                    'valid_before',spec->'reviewed_known_document_policy'->>'valid_before',
                    'latest_catalogue_discovery_available',false)
                AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'document_refs') r
                    JOIN mf_book_facts.sources s ON s.source_key=NEW.source_key AND s.content_hash=r->>'content_hash'
                    WHERE r->>'role'='catalogue' AND r->>'content_hash'=field->>'catalogue_original_sha256'
                    AND r->>'source_url'=field->>'catalogue_source_url' AND s.media_type='application/json'
                    AND s.byte_length=(spec->'reviewed_known_document_policy'->>'catalogue_bytes')::integer)
            ) IS TRUE THEN RAISE EXCEPTION 'known document provenance or exact original differs'; END IF;
        ELSIF field->>'mode'='reviewed_historical_one_pager' THEN
            IF field IS DISTINCT FROM jsonb_build_object('mode','reviewed_historical_one_pager')
                OR NEW.document->>'source_url' IS DISTINCT FROM spec->'reviewed_historical'->>'url' THEN
                RAISE EXCEPTION 'reviewed historical source provenance differs'; END IF;
        ELSIF field->>'mode'='live_catalogue' THEN
            IF NOT (field=jsonb_build_object('mode','live_catalogue','source_url',field->>'source_url')
                AND NEW.document->>'source_url'<>spec->'reviewed_historical'->>'url'
                AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'document_refs') r
                    WHERE r->>'role'='catalogue' AND r->>'source_url'=field->>'source_url')) IS TRUE THEN
                RAISE EXCEPTION 'live catalogue provenance differs'; END IF;
        ELSE RAISE EXCEPTION 'explicit source acquisition provenance required'; END IF;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'observations') o
            WHERE o->'result'->'source_acquisition' IS DISTINCT FROM field) THEN
            RAISE EXCEPTION 'fund observation source acquisition provenance differs'; END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'document_refs') LOOP
            IF NOT (item->>'role' IN('catalogue','directory','index','identity','book')
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                    AND s.content_hash=item->>'content_hash' AND s.media_type=item->>'media_type'
                    AND CASE item->>'role'
                        WHEN 'catalogue' THEN s.media_type='application/json' AND item->>'source_url' ~ '^https://www\.kotakmf\.com/api/kotakapi/forms/user/getsubheaderList/219\?year=20[0-9]{2}&month=([1-9]|1[0-2])&type=month$'
                        WHEN 'directory' THEN s.media_type='application/json' AND item->>'source_url'=spec->>'directory_url'
                        WHEN 'book' THEN s.media_type='application/pdf' AND (
                            (item->>'source_url'=spec->'reviewed_historical'->>'url' AND item->>'content_hash'=spec->'reviewed_historical'->>'sha256')
                            OR (item->>'source_url' ~ '^https://vatseelabs-s3\.kotakmf\.com/FormsDownloads/Factsheet/Factsheet-for-[A-Za-z]+-20[0-9]{2}/KotakMFFactsheet[A-Za-z]+20[0-9]{2}\.pdf$' AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN parent.media_type='application/json' AND parent.source_url ~ '^https://www\.kotakmf\.com/api/kotakapi/forms/user/getsubheaderList/219\?year=20[0-9]{2}&month=([1-9]|1[0-2])&type=month$' THEN convert_from(parent.content,'UTF8')::jsonb->'subHeaderList' ELSE '[]'::jsonb END) link WHERE parent.source_key=NEW.source_key AND item->>'source_url'='https://vatseelabs-s3.kotakmf.com/'||(link->>'content') AND link->>'headerId'='219' AND link->>'contentType'='upload')))
                        WHEN 'index' THEN s.media_type='application/json'
                            AND item->>'source_url' ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                            AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent CROSS JOIN LATERAL jsonb_array_elements(
                                CASE WHEN parent.media_type='application/json' AND parent.source_url=spec->>'directory_url'
                                    THEN convert_from(parent.content,'UTF8')::jsonb ELSE '[]'::jsonb END) x
                                WHERE parent.source_key=NEW.source_key AND parent.source_url=spec->>'directory_url'
                                AND item->>'source_url'='https://www.amfiindia.com/api/schemes/'||(x->>'scheme_id')||'/documents')
                        WHEN 'identity' THEN s.media_type='application/xml'
                            AND item->>'source_url' ~ '^https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml$'
                            AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent CROSS JOIN LATERAL jsonb_array_elements(
                                CASE WHEN parent.media_type='application/json' AND parent.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                                    THEN convert_from(parent.content,'UTF8')::jsonb->'data' ELSE '[]'::jsonb END) x
                                WHERE parent.source_key=NEW.source_key AND x->>'summaryXmlUrl'=item->>'source_url'
                                AND parent.source_url='https://www.amfiindia.com/api/schemes/'||(x->>'schemeId')||'/documents')
                        ELSE false END)) IS TRUE THEN
                RAISE EXCEPTION 'actual original document reference required'; END IF;
        END LOOP;
        IF (SELECT count(*) FROM jsonb_array_elements(NEW.document->'monthly_extractions') x
            WHERE x->>'role' IN('whole_book','reviewed_historical_one_pager')
            AND x->>'decoder_policy_hash'=NEW.document->>'decoder_policy_hash'
            AND x->>'decoder_engine_hash'=spec->>'decoder_engine_hash'
            AND x->>'extraction_hash' ~ '^[0-9a-f]{64}$'
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'document_refs') r
                WHERE r->>'role'='book' AND r->>'content_hash'=x->>'content_hash' AND r->>'source_url'=x->>'source_url'))<>1 THEN
            RAISE EXCEPTION 'one actual monthly extraction required'; END IF;
        IF (SELECT count(DISTINCT r->>'content_hash') FROM jsonb_array_elements(NEW.document->'document_refs') r WHERE r->>'role'='book')<>2 THEN
            RAISE EXCEPTION 'two actual PDF originals required'; END IF;
        IF NEW.document->>'source_url'=spec->'reviewed_historical'->>'url' AND (
            NEW.content_hash IS DISTINCT FROM spec->'reviewed_historical'->>'sha256'
            OR NEW.disclosure_as_of::text IS DISTINCT FROM spec->'reviewed_historical'->>'actual_reporting_period'
            OR NEW.document->'single_fund_historical_disclosure' IS DISTINCT FROM 'true'::jsonb) THEN
            RAISE EXCEPTION 'July one-pager cannot be labelled whole-AMC August'; END IF;
        SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer),count(DISTINCT x->'identity'->>'code')
            INTO pop,n FROM jsonb_array_elements(NEW.document->'observations') x;
        IF n<>jsonb_array_length(NEW.document->'observations') OR
            NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'unique population seal required'; END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'observations') LOOP
            IF NOT (item->'identity'->>'amc'=spec->>'amc'
                AND (item->'identity'->>'plan'=spec->>'plan' OR (item->'result'->>'status'='gap' AND item->'result'->>'reason'='unsupported_current_identity'))
                AND item->'result'->>'status' IN('usable','gap')
                AND (item->'result'->>'status'='usable')=EXISTS(SELECT 1 FROM jsonb_each(item->'result'->'fields') k WHERE k.value->>'status'='accepted')
                AND (item->'result'->>'status'='usable' OR length(item->'result'->>'reason')>0)
                AND (SELECT array_agg(key ORDER BY key) FROM jsonb_each(item->'result'->'fields'))=ARRAY['aum','base_expense_ratio','benchmark_name','fund_managers','risk_level']::text[]
            ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC observation shape differs'; END IF;

            IF item->'result'->>'status'='usable' THEN
                IF NOT (item->'identity'->>'plan'='REGULAR' AND item->'identity'->>'option' IN('GROWTH','IDCW')
                    AND jsonb_typeof(item->'result'->'identity_evidence')='array'
                    AND jsonb_array_length(item->'result'->'identity_evidence') BETWEEN 1 AND 10
                    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(item->'result'->'identity_evidence') e WHERE NOT (
                        e->>'code'=item->'identity'->>'code' AND item->'identity'->'isins' ? (e->>'isin')
                        AND e->>'locator'='/SchemeSummaryDocument/SchemeSummary/AMFI_Codes_To_be_phased_out + ISINs'
                        AND e->>'label' ~* '^Regular([[:space:]]+Plan)?\y' AND e->>'label' !~* 'direct|institutional|retail'
                        AND e->>'isin_label' ~* '^Regular([[:space:]]+Plan)?\y' AND e->>'isin_label' !~* 'direct|institutional|retail'
                        AND CASE item->'identity'->>'option' WHEN 'GROWTH' THEN e->>'label' ~* 'growth' AND e->>'label' !~* 'idcw|dividend|income distribution'
                            ELSE e->>'label' ~* 'idcw|dividend|income distribution' AND e->>'label' !~* 'growth' END
                        AND CASE item->'identity'->>'option' WHEN 'GROWTH' THEN e->>'isin_label' ~* 'growth' AND e->>'isin_label' !~* 'idcw|dividend|income distribution'
                            ELSE e->>'isin_label' ~* 'idcw|dividend|income distribution' AND e->>'isin_label' !~* 'growth' END
                        AND e ?& ARRAY['code_frequency','isin_frequency','code_mode','isin_mode','pairing_basis']
                        AND e->'code_frequency' IN ('null'::jsonb,'"daily"'::jsonb,'"weekly"'::jsonb,'"fortnightly"'::jsonb,'"monthly"'::jsonb,'"quarterly"'::jsonb,'"annual"'::jsonb)
                        AND e->'isin_frequency' IN ('null'::jsonb,'"daily"'::jsonb,'"weekly"'::jsonb,'"fortnightly"'::jsonb,'"monthly"'::jsonb,'"quarterly"'::jsonb,'"annual"'::jsonb)
                        AND e->'code_mode' IN ('null'::jsonb,'"payout"'::jsonb,'"reinvestment"'::jsonb)
                        AND e->'isin_mode' IN ('null'::jsonb,'"payout"'::jsonb,'"reinvestment"'::jsonb)
                        AND (e->'code_mode'='null'::jsonb OR e->'code_mode'=e->'isin_mode')
                        AND jsonb_typeof(e->'plan_option_codes')='array' AND e->'plan_option_codes' @> jsonb_build_array(item->'identity'->'code')
                        AND jsonb_typeof(e->'plan_option_isins')='array' AND e->'plan_option_isins' ? (e->>'isin')
                        AND ((e->>'pairing_basis'='same_labeled_frequency' AND e->'code_frequency'=e->'isin_frequency')
                            OR (e->>'pairing_basis'='unique_labeled_plan_option_pair'
                                AND e->'code_frequency'<>e->'isin_frequency' AND 'null'::jsonb IN(e->'code_frequency',e->'isin_frequency')
                                AND jsonb_array_length(e->'plan_option_codes')=1 AND jsonb_array_length(e->'plan_option_isins')=1))
                        AND jsonb_typeof(e->'code_claims')='array' AND jsonb_array_length(e->'code_claims') BETWEEN 1 AND 100
                        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(e->'code_claims') claim WHERE
                            (claim->>'code'=item->'identity'->>'code' AND claim->'variant'=jsonb_build_array('REGULAR',item->'identity'->>'option')
                                AND claim->'distribution'=jsonb_build_array(e->'code_frequency',e->'code_mode')) IS DISTINCT FROM true)
                        AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'document_refs') r
                            WHERE r->>'role'='identity' AND r->>'media_type'='application/xml'
                            AND r->>'content_hash'=e->>'source_hash' AND r->>'source_url'=e->>'source_url')
                        AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                            AND s.content_hash=e->>'index_hash' AND s.media_type='application/json'
                            AND s.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                            AND EXISTS(SELECT 1 FROM jsonb_array_elements(convert_from(s.content,'UTF8')::jsonb->'data') d
                                WHERE d->>'summaryXmlUrl'=e->>'source_url'))
                    ) IS TRUE)
                ) IS TRUE THEN RAISE EXCEPTION 'Kotak exact XML identity evidence differs'; END IF;
            END IF;
            FOR pair IN SELECT key,value FROM jsonb_each(item->'result'->'fields') LOOP
                field=pair.value;
                IF NOT (field->>'disclosure_as_of'=NEW.disclosure_as_of::text AND field->>'status' IN('accepted','unavailable')) IS TRUE THEN
                    RAISE EXCEPTION 'field status/date differs'; END IF;
                IF field->>'status'='unavailable' THEN
                    IF field->'value' IS DISTINCT FROM 'null'::jsonb OR COALESCE(length(field->>'reason'),0)=0 THEN
                        RAISE EXCEPTION 'unavailable field requires null and gap reason'; END IF;
                ELSE
                    IF NOT (length(field->>'source_anchor')>0 AND length(field->>'raw_value')>0
                        AND field->>'detail_page' ~ '^[1-9][0-9]{0,2}$'
                        AND field->>'detail_text_hash' ~ '^[0-9a-f]{64}$'
                        AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'monthly_extractions') e
                            WHERE e->>'content_hash'=field->>'content_hash' AND e->>'source_url'=field->>'source_url')
                        AND field->>'date_basis' IN('explicit_field_date','document_context_not_field_effective_date')
                        AND CASE WHEN field->>'date_basis'='explicit_field_date' THEN field->>'field_effective_date'=NEW.disclosure_as_of::text
                            ELSE field->'field_effective_date'='null'::jsonb END
                        AND CASE pair.key
                            WHEN 'benchmark_name' THEN field->>'unit'='name' AND field->>'scope'='fund' AND jsonb_typeof(field->'value')='string' AND length(btrim(field->>'value')) BETWEEN 1 AND 250
                                AND EXISTS(SELECT 1 FROM jsonb_array_elements_text(spec->'parser_policy'->'reviewed_primary_tri_labels') label
                                    WHERE lower(label)=lower(regexp_replace(field->>'value','[[:space:]]*\(Tier[[:space:]]*1\)$','','i')))
                            WHEN 'fund_managers' THEN field->>'unit'='names' AND field->>'scope'='fund' AND CASE WHEN jsonb_typeof(field->'value')='array' THEN jsonb_array_length(field->'value') BETWEEN 1 AND 15 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(field->'value') z WHERE jsonb_typeof(z)<>'string' OR length(btrim(z#>>'{}')) NOT BETWEEN 1 AND 150) ELSE false END
                            WHEN 'base_expense_ratio' THEN field->>'unit'='percent' AND field->>'scope'='regular_plan' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,2}(\.[0-9]{1,4})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 10 ELSE false END
                            WHEN 'aum' THEN field->>'unit'='INR_crore' AND field->>'scope'='fund' AND field->>'date_basis'='explicit_field_date' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,13}(\.[0-9]{1,6})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 1000000000000 ELSE false END
                            WHEN 'risk_level' THEN field->>'unit'='risk_category' AND field->>'scope'='fund' AND field->>'value' IN('Low','Low to Moderate','Moderate','Moderately High','High','Very High')
                            ELSE false END
                    ) IS TRUE THEN RAISE EXCEPTION 'accepted field type/unit/date/evidence differs'; END IF;
                END IF;
            END LOOP;
        END LOOP;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME<>'runs' THEN RAISE EXCEPTION 'unmodelled book input'; END IF;
    IF NOT (NEW.run_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'run_id'),'UTF8')),'hex')
        AND NEW.document->>'run_id'=NEW.run_id AND NEW.document->>'format'='official-amc-book-run-v1'
        AND NEW.document->>'source_key'=NEW.source_key AND NEW.document->>'execution_id'=NEW.execution_id
        AND NEW.document->>'status'=NEW.status AND (NEW.document->>'observed_at')::timestamptz=NEW.observed_at
        AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
        AND NEW.document->'source_rows_changed'='0'::jsonb AND NEW.document->'all_amc_data_complete'='false'::jsonb
    ) IS TRUE THEN RAISE EXCEPTION 'book run seal differs'; END IF;
    IF NEW.status='failed' THEN
        IF NEW.document->'batch_ids' IS DISTINCT FROM '[]'::jsonb THEN RAISE EXCEPTION 'failed receipt cannot publish'; END IF;
        RETURN NEW;
    END IF;
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN RAISE EXCEPTION 'governed publication lock required'; END IF;
    SELECT jsonb_agg(jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,'scheme_name',f.scheme_name,
        'plan',f.plan_type,'option',f.option_type,'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
            (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
        ORDER BY f.amfi_scheme_code) INTO pop FROM mf.funds f JOIN mf.amcs a USING(amc_id)
        WHERE a.amfi_amc_name=spec->>'amc' AND f.in_scope;
    IF pop IS NULL OR jsonb_array_length(pop) NOT BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
        OR NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'complete current in-scope AMC population required'; END IF;
    SELECT jsonb_agg(batch_id ORDER BY disclosure_as_of DESC,batch_id),count(*) INTO ids,n
        FROM mf_book_facts.batches WHERE source_key=NEW.source_key AND NEW.document->'batch_ids' ? batch_id;
    IF ids IS DISTINCT FROM NEW.document->'batch_ids' OR n<>(spec->>'period_count')::integer OR
        (SELECT count(DISTINCT disclosure_as_of) FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id)<>n THEN
        RAISE EXCEPTION 'two distinct complete ordered periods required'; END IF;
    FOR b IN SELECT * FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id LOOP
        IF (SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer) FROM jsonb_array_elements(b.document->'observations') x) IS DISTINCT FROM pop
            OR b.disclosure_as_of>NEW.observed_at::date OR b.document->>'specification_hash'<>NEW.document->>'specification_hash' THEN
            RAISE EXCEPTION 'batch current identity/specification differs'; END IF;
        IF b.document->'source_acquisition'->>'mode'='reviewed_known_document_not_live_catalogue' AND (
            (NEW.observed_at AT TIME ZONE 'UTC')::date<(spec->'reviewed_known_document_policy'->>'reviewed_on')::date
            OR (NEW.observed_at AT TIME ZONE 'UTC')::date>=(spec->'reviewed_known_document_policy'->>'valid_before')::date
            OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'availability') gap
                WHERE gap->>'expected_period'=b.disclosure_as_of::text
                AND gap->>'reason'='ordinary_source_unavailable_reviewed_known_document_used'
                AND gap->'latest_catalogue_discovery_available'='false'::jsonb)) THEN
            RAISE EXCEPTION 'known document use outside reviewed window or missing discovery warning'; END IF;
    END LOOP;
    IF (SELECT dataset_version::text FROM mf.dataset_summary WHERE singleton) IS DISTINCT FROM NEW.document->>'dataset_version' THEN
        RAISE EXCEPTION 'run live dataset receipt differs'; END IF;
    IF NOT (jsonb_typeof(NEW.document->'availability')='array' AND jsonb_array_length(NEW.document->'availability')<=256) IS TRUE THEN
        RAISE EXCEPTION 'bounded explicit source availability required'; END IF;
    FOR b IN SELECT document FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id LOOP
        IF b.document->'expected_recent_reporting_periods' IS DISTINCT FROM jsonb_build_array(
            (date_trunc('month',NEW.observed_at AT TIME ZONE 'UTC')-interval '1 day')::date::text,
            (date_trunc('month',NEW.observed_at AT TIME ZONE 'UTC')-interval '1 month 1 day')::date::text) THEN
            RAISE EXCEPTION 'actual run recent-month expectations differ'; END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(b.document->'expected_recent_reporting_periods') LOOP
            IF NOT EXISTS(SELECT 1 FROM mf_book_facts.batches current_batch
                WHERE NEW.document->'batch_ids' ? current_batch.batch_id AND current_batch.disclosure_as_of::text=item#>>'{}')
                AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'availability') gap
                    WHERE gap->>'expected_period'=item#>>'{}' AND gap->>'reason'='expected_recent_period_not_acquired') THEN
                RAISE EXCEPTION 'missing expected month must remain explicit'; END IF;
        END LOOP;
    END LOOP;
    -- Catalog originals are retained byte-for-byte in the same append-only DB.
    IF NOT (jsonb_typeof(NEW.document->'catalog_hashes')='array' AND jsonb_array_length(NEW.document->'catalog_hashes') BETWEEN (spec->>'minimum_catalogue_originals')::integer AND 203
        AND (SELECT count(DISTINCT content_hash) FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.media_type IN('text/html','application/json') AND NEW.document->'catalog_hashes' ? s.content_hash)=jsonb_array_length(NEW.document->'catalog_hashes')) IS TRUE THEN
        RAISE EXCEPTION 'retained catalogue originals required'; END IF;

    SELECT count(DISTINCT r->>'content_hash') INTO n FROM mf_book_facts.batches archived_batch
        CROSS JOIN LATERAL jsonb_array_elements(archived_batch.document->'document_refs') r
        WHERE NEW.document->'batch_ids' ? archived_batch.batch_id AND r->>'media_type' IN('application/pdf','application/xml');
    IF jsonb_array_length(NEW.document->'archives') IS DISTINCT FROM n THEN RAISE EXCEPTION 'all Kotak PDF/XML archives required'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'archives') LOOP
        IF NOT (item->>'generation' ~ '^[1-9][0-9]*$'
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                AND s.content_hash=item->>'sha256' AND s.byte_length=(item->>'size')::integer
                AND s.media_type IN('application/pdf','application/xml')
                AND item->>'uri'='gs://tlws_mf_data_source/'||(spec->>'archive_prefix')||s.content_hash||
                    CASE s.media_type WHEN 'application/pdf' THEN '.pdf' ELSE '.xml' END)
        ) IS TRUE THEN RAISE EXCEPTION 'Kotak original archive binding differs'; END IF;
    END LOOP;
    IF (SELECT count(DISTINCT x->>'sha256') FROM jsonb_array_elements(NEW.document->'archives') x
        WHERE EXISTS(SELECT 1 FROM mf_book_facts.batches archived_batch
            CROSS JOIN LATERAL jsonb_array_elements(archived_batch.document->'document_refs') r
            WHERE NEW.document->'batch_ids' ? archived_batch.batch_id AND r->>'content_hash'=x->>'sha256'
            AND r->>'media_type' IN('application/pdf','application/xml')))<>n THEN
        RAISE EXCEPTION 'distinct complete Kotak archives required'; END IF;
    IF (NEW.status='unchanged') IS DISTINCT FROM EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.source_key=NEW.source_key
        AND r.status<>'failed' AND r.document->'batch_ids'=NEW.document->'batch_ids') THEN RAISE EXCEPTION 'new versus replay status differs'; END IF;
    RETURN NEW;
    END IF;

    -- Registry v1 interpretation below remains byte-for-byte frozen.
    IF spec->>'adapter'='sbi-full-book-v1' THEN

    IF TG_TABLE_NAME='sources' THEN
        IF NOT (
            (NEW.byte_length<=1048576 AND NEW.media_type='text/html' AND NEW.source_url=spec->>'catalogue_api')
            OR (NEW.byte_length<=1048576 AND NEW.media_type='application/json' AND NEW.source_url=spec->>'directory_url')
            OR (NEW.byte_length<=1048576 AND NEW.media_type='application/json'
                AND NEW.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources d CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN d.media_type='application/json' AND d.source_url=spec->>'directory_url'
                    THEN convert_from(d.content,'UTF8')::jsonb ELSE '[]'::jsonb END) x
                    WHERE d.source_key=NEW.source_key AND d.source_url=spec->>'directory_url'
                    AND NEW.source_url='https://www.amfiindia.com/api/schemes/'||(x->>'scheme_id')||'/documents'))
            OR (NEW.byte_length<=1048576 AND NEW.media_type='application/xml'
                AND NEW.source_url ~ '^https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml$'
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources d CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN d.media_type='application/json' AND d.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                    THEN convert_from(d.content,'UTF8')::jsonb->'data' ELSE '[]'::jsonb END) x
                    WHERE d.source_key=NEW.source_key
                    AND x->>'summaryXmlUrl'=NEW.source_url
                    AND d.source_url='https://www.amfiindia.com/api/schemes/'||(x->>'schemeId')||'/documents'))
            OR (NEW.media_type='application/pdf' AND substring(NEW.content FROM 1 FOR 5)=convert_to('%PDF-','UTF8')
                AND NEW.source_url ~ '^https://www\.sbimf\.com/docs/default-source/scheme-factsheets/(all-sbimf-schemes|sbi-mf-passives-\(index-etf-fof\))-factsheet-[a-z]+-20[0-9]{2}\.pdf(\?sfvrsn=[A-Za-z0-9_]+)?$'
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources c WHERE c.source_key=NEW.source_key
                    AND c.media_type='text/html' AND c.source_url=spec->>'catalogue_api'
                    AND position(NEW.source_url in convert_from(c.content,'UTF8'))>0))
        ) IS TRUE THEN RAISE EXCEPTION 'SBI original URL/media/parent evidence differs'; END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='batches' THEN
        IF NOT (NEW.batch_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'batch_id'),'UTF8')),'hex')
            AND NEW.document->>'batch_id'=NEW.batch_id
            AND NEW.document->>'format'='official-amc-book-batch-v2'
            AND NEW.document->>'source_key'=NEW.source_key
            AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
            AND NEW.document->>'content_hash'=NEW.content_hash
            AND NEW.document->>'identity_content_hash'=NEW.identity_content_hash
            AND NEW.document->>'disclosure_as_of'=NEW.disclosure_as_of::text
            AND NEW.disclosure_as_of=(date_trunc('month',NEW.disclosure_as_of)+interval '1 month - 1 day')::date
            AND NEW.document->>'verification_status'='needs_verification'
            AND NEW.document->'source_rows_changed'='0'::jsonb
            AND NEW.document->'canonical_analytics_replaced'='false'::jsonb
            AND NEW.document->'whole_factsheet_semantic_coverage'='false'::jsonb
            AND NEW.document->'adjusted_idcw_analytics_enabled'='false'::jsonb
            AND NEW.document->>'decoder_policy_hash'=spec->'parser_policy'->>'decoder_policy_hash'
            AND NEW.document->>'extraction_hash' ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(NEW.document->'observations')='array'
            AND jsonb_array_length(NEW.document->'observations') BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.content_hash=NEW.content_hash
                AND s.media_type='application/pdf' AND starts_with(NEW.document->>'source_url',spec->>'book_prefix'))
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'monthly_extractions') e
                WHERE e->>'role'='active' AND e->>'content_hash'=NEW.content_hash AND e->>'source_url'=NEW.document->>'source_url')
            AND NEW.document->>'identity_content_role'='actual_amfi_directory_json_not_pdf'
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                AND s.content_hash=NEW.identity_content_hash AND s.source_url=spec->>'directory_url' AND s.media_type='application/json')
            AND NEW.document->>'extraction_hash'=encode(sha256(convert_to(mf_review.canonical_json(NEW.document->'monthly_extractions'),'UTF8')),'hex')
            AND jsonb_typeof(NEW.document->'document_refs')='array'
            AND jsonb_array_length(NEW.document->'document_refs') BETWEEN 7 AND 512
            AND jsonb_array_length(NEW.document->'monthly_extractions')=2
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'observations') o WHERE o->'result'->>'status'='usable')
        ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC batch source/policy/seal differs'; END IF;

        FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'document_refs') LOOP
            IF NOT (item->>'role' IN('catalogue','directory','index','identity','book')
                AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                    AND s.content_hash=item->>'content_hash' AND s.media_type=item->>'media_type'
                    AND CASE item->>'role'
                        WHEN 'catalogue' THEN s.media_type='text/html' AND item->>'source_url'=spec->>'catalogue_api'
                        WHEN 'directory' THEN s.media_type='application/json' AND item->>'source_url'=spec->>'directory_url'
                        WHEN 'book' THEN s.media_type='application/pdf'
                            AND item->>'source_url' ~ '^https://www\.sbimf\.com/docs/default-source/scheme-factsheets/(all-sbimf-schemes|sbi-mf-passives-\(index-etf-fof\))-factsheet-[a-z]+-20[0-9]{2}\.pdf(\?sfvrsn=[A-Za-z0-9_]+)?$'
                            AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent WHERE parent.source_key=NEW.source_key
                                AND parent.media_type='text/html' AND parent.source_url=spec->>'catalogue_api'
                                AND position(item->>'source_url' in convert_from(parent.content,'UTF8'))>0)
                        WHEN 'index' THEN s.media_type='application/json'
                            AND item->>'source_url' ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                            AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent CROSS JOIN LATERAL jsonb_array_elements(
                                CASE WHEN parent.media_type='application/json' AND parent.source_url=spec->>'directory_url'
                                    THEN convert_from(parent.content,'UTF8')::jsonb ELSE '[]'::jsonb END) x
                                WHERE parent.source_key=NEW.source_key AND parent.source_url=spec->>'directory_url'
                                AND item->>'source_url'='https://www.amfiindia.com/api/schemes/'||(x->>'scheme_id')||'/documents')
                        WHEN 'identity' THEN s.media_type='application/xml'
                            AND item->>'source_url' ~ '^https://portal\.amfiindia\.com/spages/SSD_[1-9][0-9]{0,7}\.xml$'
                            AND EXISTS(SELECT 1 FROM mf_book_facts.sources parent CROSS JOIN LATERAL jsonb_array_elements(
                                CASE WHEN parent.media_type='application/json' AND parent.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                                    THEN convert_from(parent.content,'UTF8')::jsonb->'data' ELSE '[]'::jsonb END) x
                                WHERE parent.source_key=NEW.source_key AND x->>'summaryXmlUrl'=item->>'source_url'
                                AND parent.source_url='https://www.amfiindia.com/api/schemes/'||(x->>'schemeId')||'/documents')
                        ELSE false END)) IS TRUE THEN
                RAISE EXCEPTION 'actual original document reference required'; END IF;
        END LOOP;
        IF (SELECT count(DISTINCT x->>'role') FROM jsonb_array_elements(NEW.document->'monthly_extractions') x
            WHERE x->>'role' IN('active','passive') AND x->>'decoder_policy_hash'=NEW.document->>'decoder_policy_hash'
            AND x->>'extraction_hash' ~ '^[0-9a-f]{64}$'
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'document_refs') r
                WHERE r->>'role'='book' AND r->>'content_hash'=x->>'content_hash'
                AND r->>'source_url'=x->>'source_url'))<>2 THEN
            RAISE EXCEPTION 'two real monthly extractions required'; END IF;
        SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer),count(DISTINCT x->'identity'->>'code')
            INTO pop,n FROM jsonb_array_elements(NEW.document->'observations') x;
        IF n<>jsonb_array_length(NEW.document->'observations') OR
            NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'unique population seal required'; END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'observations') LOOP
            IF NOT (item->'identity'->>'amc'=spec->>'amc'
                AND (item->'identity'->>'plan'=spec->>'plan' OR (item->'result'->>'status'='gap' AND item->'result'->>'reason'='unsupported_current_identity'))
                AND item->'result'->>'status' IN('usable','gap')
                AND (item->'result'->>'status'='usable')=EXISTS(SELECT 1 FROM jsonb_each(item->'result'->'fields') k WHERE k.value->>'status'='accepted')
                AND (item->'result'->>'status'='usable' OR length(item->'result'->>'reason')>0)
                AND (SELECT array_agg(key ORDER BY key) FROM jsonb_each(item->'result'->'fields'))=ARRAY['aum','base_expense_ratio','benchmark_name','fund_managers','risk_level']::text[]
            ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC observation shape differs'; END IF;

            IF item->'result'->>'status'='usable' THEN
                IF NOT (item->'identity'->>'plan'='REGULAR' AND item->'identity'->>'option' IN('GROWTH','IDCW')
                    AND jsonb_typeof(item->'result'->'identity_evidence')='array'
                    AND jsonb_array_length(item->'result'->'identity_evidence') BETWEEN 1 AND 10
                    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(item->'result'->'identity_evidence') e WHERE NOT (
                        e->>'code'=item->'identity'->>'code' AND item->'identity'->'isins' ? (e->>'isin')
                        AND e->>'locator' ~ '^/root/Options_Names/item\[[1-9][0-9]{0,2}\]$'
                        AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'document_refs') r
                            WHERE r->>'role'='identity' AND r->>'media_type'='application/xml'
                            AND r->>'content_hash'=e->>'source_hash' AND r->>'source_url'=e->>'source_url')
                        AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                            AND s.content_hash=e->>'index_hash' AND s.media_type='application/json'
                            AND s.source_url ~ '^https://www\.amfiindia\.com/api/schemes/[1-9][0-9]{0,7}/documents$'
                            AND EXISTS(SELECT 1 FROM jsonb_array_elements(convert_from(s.content,'UTF8')::jsonb->'data') d
                                WHERE d->>'summaryXmlUrl'=e->>'source_url'))
                    ) IS TRUE)
                ) IS TRUE THEN RAISE EXCEPTION 'SBI exact XML identity evidence differs'; END IF;
            END IF;
            FOR pair IN SELECT key,value FROM jsonb_each(item->'result'->'fields') LOOP
                field=pair.value;
                IF NOT (field->>'disclosure_as_of'=NEW.disclosure_as_of::text AND field->>'status' IN('accepted','unavailable')) IS TRUE THEN
                    RAISE EXCEPTION 'field status/date differs'; END IF;
                IF field->>'status'='unavailable' THEN
                    IF field->'value' IS DISTINCT FROM 'null'::jsonb OR COALESCE(length(field->>'reason'),0)=0 THEN
                        RAISE EXCEPTION 'unavailable field requires null and gap reason'; END IF;
                ELSE
                    IF NOT (length(field->>'source_anchor')>0 AND length(field->>'raw_value')>0
                        AND field->>'detail_page' ~ '^[1-9][0-9]{0,2}$'
                        AND field->>'detail_text_hash' ~ '^[0-9a-f]{64}$'
                        AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.document->'monthly_extractions') e
                            WHERE e->>'content_hash'=field->>'content_hash' AND e->>'source_url'=field->>'source_url')
                        AND field->>'date_basis' IN('explicit_field_date','document_context_not_field_effective_date')
                        AND CASE WHEN field->>'date_basis'='explicit_field_date' THEN field->>'field_effective_date'=NEW.disclosure_as_of::text
                            ELSE field->'field_effective_date'='null'::jsonb END
                        AND CASE pair.key
                            WHEN 'benchmark_name' THEN field->>'unit'='name' AND field->>'scope'='fund' AND jsonb_typeof(field->'value')='string' AND length(btrim(field->>'value')) BETWEEN 1 AND 250
                            WHEN 'fund_managers' THEN field->>'unit'='names' AND field->>'scope'='fund' AND CASE WHEN jsonb_typeof(field->'value')='array' THEN jsonb_array_length(field->'value') BETWEEN 1 AND 15 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(field->'value') z WHERE jsonb_typeof(z)<>'string' OR length(btrim(z#>>'{}')) NOT BETWEEN 1 AND 150) ELSE false END
                            WHEN 'base_expense_ratio' THEN field->>'unit'='percent' AND field->>'scope'='regular_plan' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,2}(\.[0-9]{1,4})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 10 ELSE false END
                            WHEN 'aum' THEN field->>'unit'='INR_crore' AND field->>'scope'='fund' AND field->>'date_basis'='explicit_field_date' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,13}(\.[0-9]{1,6})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 1000000000000 ELSE false END
                            WHEN 'risk_level' THEN field->>'unit'='risk_category' AND field->>'scope'='fund' AND field->>'value' IN('Low','Low to Moderate','Moderate','Moderately High','High','Very High')
                            ELSE false END
                    ) IS TRUE THEN RAISE EXCEPTION 'accepted field type/unit/date/evidence differs'; END IF;
                END IF;
            END LOOP;
        END LOOP;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME<>'runs' THEN RAISE EXCEPTION 'unmodelled book input'; END IF;
    IF NOT (NEW.run_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'run_id'),'UTF8')),'hex')
        AND NEW.document->>'run_id'=NEW.run_id AND NEW.document->>'format'='official-amc-book-run-v1'
        AND NEW.document->>'source_key'=NEW.source_key AND NEW.document->>'execution_id'=NEW.execution_id
        AND NEW.document->>'status'=NEW.status AND (NEW.document->>'observed_at')::timestamptz=NEW.observed_at
        AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
        AND NEW.document->'source_rows_changed'='0'::jsonb AND NEW.document->'all_amc_data_complete'='false'::jsonb
    ) IS TRUE THEN RAISE EXCEPTION 'book run seal differs'; END IF;
    IF NEW.status='failed' THEN
        IF NEW.document->'batch_ids' IS DISTINCT FROM '[]'::jsonb THEN RAISE EXCEPTION 'failed receipt cannot publish'; END IF;
        RETURN NEW;
    END IF;
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN RAISE EXCEPTION 'governed publication lock required'; END IF;
    SELECT jsonb_agg(jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,'scheme_name',f.scheme_name,
        'plan',f.plan_type,'option',f.option_type,'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
            (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
        ORDER BY f.amfi_scheme_code) INTO pop FROM mf.funds f JOIN mf.amcs a USING(amc_id)
        WHERE a.amfi_amc_name=spec->>'amc' AND f.in_scope;
    IF pop IS NULL OR jsonb_array_length(pop) NOT BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
        OR NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'complete current in-scope AMC population required'; END IF;
    SELECT jsonb_agg(batch_id ORDER BY disclosure_as_of DESC,batch_id),count(*) INTO ids,n
        FROM mf_book_facts.batches WHERE source_key=NEW.source_key AND NEW.document->'batch_ids' ? batch_id;
    IF ids IS DISTINCT FROM NEW.document->'batch_ids' OR n<>(spec->>'period_count')::integer OR
        (SELECT count(DISTINCT disclosure_as_of) FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id)<>n THEN
        RAISE EXCEPTION 'two distinct complete ordered periods required'; END IF;
    FOR b IN SELECT * FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id LOOP
        IF (SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer) FROM jsonb_array_elements(b.document->'observations') x) IS DISTINCT FROM pop
            OR b.disclosure_as_of>NEW.observed_at::date OR b.document->>'specification_hash'<>NEW.document->>'specification_hash' THEN
            RAISE EXCEPTION 'batch current identity/specification differs'; END IF;
    END LOOP;
    IF (SELECT dataset_version::text FROM mf.dataset_summary WHERE singleton) IS DISTINCT FROM NEW.document->>'dataset_version' THEN
        RAISE EXCEPTION 'run live dataset receipt differs'; END IF;
    -- Catalog originals are retained byte-for-byte in the same append-only DB.
    IF NOT (jsonb_typeof(NEW.document->'catalog_hashes')='array' AND jsonb_array_length(NEW.document->'catalog_hashes') BETWEEN (spec->>'minimum_catalogue_originals')::integer AND 202
        AND (SELECT count(DISTINCT content_hash) FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.media_type IN('text/html','application/json') AND NEW.document->'catalog_hashes' ? s.content_hash)=jsonb_array_length(NEW.document->'catalog_hashes')) IS TRUE THEN
        RAISE EXCEPTION 'retained catalogue originals required'; END IF;

    SELECT count(DISTINCT r->>'content_hash') INTO n FROM mf_book_facts.batches archived_batch
        CROSS JOIN LATERAL jsonb_array_elements(archived_batch.document->'document_refs') r
        WHERE NEW.document->'batch_ids' ? archived_batch.batch_id AND r->>'media_type' IN('application/pdf','application/xml');
    IF jsonb_array_length(NEW.document->'archives') IS DISTINCT FROM n THEN RAISE EXCEPTION 'all SBI PDF/XML archives required'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'archives') LOOP
        IF NOT (item->>'generation' ~ '^[1-9][0-9]*$'
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key
                AND s.content_hash=item->>'sha256' AND s.byte_length=(item->>'size')::integer
                AND s.media_type IN('application/pdf','application/xml')
                AND item->>'uri'='gs://tlws_mf_data_source/'||(spec->>'archive_prefix')||s.content_hash||
                    CASE s.media_type WHEN 'application/pdf' THEN '.pdf' ELSE '.xml' END)
        ) IS TRUE THEN RAISE EXCEPTION 'SBI original archive binding differs'; END IF;
    END LOOP;
    IF (SELECT count(DISTINCT x->>'sha256') FROM jsonb_array_elements(NEW.document->'archives') x
        WHERE EXISTS(SELECT 1 FROM mf_book_facts.batches archived_batch
            CROSS JOIN LATERAL jsonb_array_elements(archived_batch.document->'document_refs') r
            WHERE NEW.document->'batch_ids' ? archived_batch.batch_id AND r->>'content_hash'=x->>'sha256'
            AND r->>'media_type' IN('application/pdf','application/xml')))<>n THEN
        RAISE EXCEPTION 'distinct complete SBI archives required'; END IF;
    IF (NEW.status='unchanged') IS DISTINCT FROM EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.source_key=NEW.source_key
        AND r.status<>'failed' AND r.document->'batch_ids'=NEW.document->'batch_ids') THEN RAISE EXCEPTION 'new versus replay status differs'; END IF;
    RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='sources' THEN
        IF NOT ((NEW.media_type='application/pdf' AND substring(NEW.content FROM 1 FOR 5)=convert_to('%PDF-','UTF8') AND
            ((starts_with(NEW.source_url,spec->>'book_prefix') AND NEW.source_url ~ '/[A-Za-z0-9_.-]+\.pdf$')
             OR EXISTS(SELECT 1 FROM jsonb_array_elements(spec->'identity_documents') x
                WHERE x->>'url'=NEW.source_url AND x->>'sha256'=NEW.content_hash)))
            OR (NEW.byte_length<=1048576 AND ((NEW.media_type='text/html' AND NEW.source_url=spec->>'catalogue_landing')
                OR (NEW.media_type='application/json' AND NEW.source_url ~ (spec->>'catalogue_api_pattern'))))) IS TRUE THEN
            RAISE EXCEPTION 'original URL, media or identity pin not authorized';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='batches' THEN
        IF NOT (NEW.batch_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'batch_id'),'UTF8')),'hex')
            AND NEW.document->>'batch_id'=NEW.batch_id
            AND NEW.document->>'format'='official-amc-book-batch-v1'
            AND NEW.document->>'source_key'=NEW.source_key
            AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
            AND NEW.document->>'content_hash'=NEW.content_hash
            AND NEW.document->>'identity_content_hash'=NEW.identity_content_hash
            AND NEW.document->>'disclosure_as_of'=NEW.disclosure_as_of::text
            AND NEW.disclosure_as_of=(date_trunc('month',NEW.disclosure_as_of)+interval '1 month - 1 day')::date
            AND NEW.document->>'verification_status'='needs_verification'
            AND NEW.document->'source_rows_changed'='0'::jsonb
            AND NEW.document->'canonical_analytics_replaced'='false'::jsonb
            AND NEW.document->'whole_factsheet_semantic_coverage'='false'::jsonb
            AND NEW.document->'adjusted_idcw_analytics_enabled'='false'::jsonb
            AND NEW.document->>'decoder_policy_hash'=spec->'parser_policy'->>'decoder_policy_hash'
            AND NEW.document->>'extraction_hash' ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(NEW.document->'observations')='array'
            AND jsonb_array_length(NEW.document->'observations') BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.content_hash=NEW.content_hash
                AND s.source_url=NEW.document->>'source_url' AND s.media_type='application/pdf' AND starts_with(s.source_url,spec->>'book_prefix'))
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(spec->'identity_documents') x WHERE x->>'sha256'=NEW.identity_content_hash)
        ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC batch source/policy/seal differs'; END IF;
        SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer),count(DISTINCT x->'identity'->>'code')
            INTO pop,n FROM jsonb_array_elements(NEW.document->'observations') x;
        IF n<>jsonb_array_length(NEW.document->'observations') OR
            NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
            RAISE EXCEPTION 'unique population seal required'; END IF;
        FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'observations') LOOP
            IF NOT (item->'identity'->>'amc'=spec->>'amc'
                AND (item->'identity'->>'plan'=spec->>'plan' OR (item->'result'->>'status'='gap' AND item->'result'->>'reason'='unsupported_current_identity'))
                AND item->'result'->>'status' IN('usable','gap')
                AND (item->'result'->>'status'='usable')=EXISTS(SELECT 1 FROM jsonb_each(item->'result'->'fields') k WHERE k.value->>'status'='accepted')
                AND (item->'result'->>'status'='usable' OR length(item->'result'->>'reason')>0)
                AND (SELECT array_agg(key ORDER BY key) FROM jsonb_each(item->'result'->'fields'))=ARRAY['aum','base_expense_ratio','benchmark_name','fund_managers','risk_level']::text[]
            ) IS TRUE THEN RAISE EXCEPTION 'whole-AMC observation shape differs'; END IF;
            IF item->'result'->>'status'='usable' THEN
                IF NOT (jsonb_typeof(item->'result'->'identity_evidence')='array'
                    AND jsonb_array_length(item->'result'->'identity_evidence') BETWEEN 1 AND 10
                    AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(item->'result'->'identity_evidence') e WHERE NOT (
                        e->>'code'=item->'identity'->>'code' AND item->'identity'->'isins' ? (e->>'isin')
                        AND e->>'source_hash'=NEW.identity_content_hash AND e->>'text_hash' ~ '^[0-9a-f]{64}$'
                        AND e->>'page' ~ '^[1-9][0-9]{0,2}$') IS TRUE)
                    AND item->'result'->>'detail_text_hash' ~ '^[0-9a-f]{64}$'
                    AND item->'result'->>'detail_page' ~ '^[1-9][0-9]{0,2}$'
                ) IS TRUE THEN RAISE EXCEPTION 'usable fields require exact code/ISIN page evidence'; END IF;
            END IF;
            FOR pair IN SELECT key,value FROM jsonb_each(item->'result'->'fields') LOOP
                field=pair.value;
                IF NOT (field->>'disclosure_as_of'=NEW.disclosure_as_of::text AND field->>'status' IN('accepted','unavailable')) IS TRUE THEN
                    RAISE EXCEPTION 'field status/date differs'; END IF;
                IF field->>'status'='unavailable' THEN
                    IF field->'value' IS DISTINCT FROM 'null'::jsonb OR COALESCE(length(field->>'reason'),0)=0 THEN
                        RAISE EXCEPTION 'unavailable field requires null and gap reason'; END IF;
                ELSE
                    IF NOT (length(field->>'source_anchor')>0 AND length(field->>'raw_value')>0
                        AND field->>'date_basis' IN('explicit_field_date','document_context_not_field_effective_date')
                        AND CASE WHEN field->>'date_basis'='explicit_field_date' THEN field->>'field_effective_date'=NEW.disclosure_as_of::text
                            ELSE field->'field_effective_date'='null'::jsonb END
                        AND CASE pair.key
                            WHEN 'benchmark_name' THEN field->>'unit'='name' AND field->>'scope'='fund' AND jsonb_typeof(field->'value')='string' AND length(btrim(field->>'value')) BETWEEN 1 AND 250
                            WHEN 'fund_managers' THEN field->>'unit'='names' AND field->>'scope'='fund' AND CASE WHEN jsonb_typeof(field->'value')='array' THEN jsonb_array_length(field->'value') BETWEEN 1 AND 15 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(field->'value') z WHERE jsonb_typeof(z)<>'string' OR length(btrim(z#>>'{}')) NOT BETWEEN 1 AND 150) ELSE false END
                            WHEN 'base_expense_ratio' THEN field->>'unit'='percent' AND field->>'scope'='regular_plan' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,2}(\.[0-9]{1,4})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 10 ELSE false END
                            WHEN 'aum' THEN field->>'unit'='INR_crore' AND field->>'scope'='fund' AND field->>'date_basis'='explicit_field_date' AND jsonb_typeof(field->'value')='string' AND CASE WHEN field->>'value' ~ '^[0-9]{1,13}(\.[0-9]{1,6})?$' THEN (field->>'value')::numeric BETWEEN 0 AND 1000000000000 ELSE false END
                            WHEN 'risk_level' THEN field->>'unit'='risk_category' AND field->>'scope'='fund' AND field->>'value' IN('Low','Low to Moderate','Moderate','Moderately High','High','Very High')
                            ELSE false END
                    ) IS TRUE THEN RAISE EXCEPTION 'accepted field type/unit/date/evidence differs'; END IF;
                END IF;
            END LOOP;
        END LOOP;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME<>'runs' THEN RAISE EXCEPTION 'unmodelled book input'; END IF;
    IF NOT (NEW.run_id=encode(sha256(convert_to(mf_review.canonical_json(NEW.document-'run_id'),'UTF8')),'hex')
        AND NEW.document->>'run_id'=NEW.run_id AND NEW.document->>'format'='official-amc-book-run-v1'
        AND NEW.document->>'source_key'=NEW.source_key AND NEW.document->>'execution_id'=NEW.execution_id
        AND NEW.document->>'status'=NEW.status AND (NEW.document->>'observed_at')::timestamptz=NEW.observed_at
        AND NEW.document->>'specification_hash'=encode(sha256(convert_to(mf_review.canonical_json(spec),'UTF8')),'hex')
        AND NEW.document->'source_rows_changed'='0'::jsonb AND NEW.document->'all_amc_data_complete'='false'::jsonb
    ) IS TRUE THEN RAISE EXCEPTION 'book run seal differs'; END IF;
    IF NEW.status='failed' THEN
        IF NEW.document->'batch_ids' IS DISTINCT FROM '[]'::jsonb THEN RAISE EXCEPTION 'failed receipt cannot publish'; END IF;
        RETURN NEW;
    END IF;
    IF NOT pg_try_advisory_xact_lock(1413423425,1) THEN RAISE EXCEPTION 'governed publication lock required'; END IF;
    SELECT jsonb_agg(jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,'scheme_name',f.scheme_name,
        'plan',f.plan_type,'option',f.option_type,'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM
            (SELECT DISTINCT btrim(x) AS i FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) x WHERE x IS NOT NULL) identifiers),'[]'::jsonb))
        ORDER BY f.amfi_scheme_code) INTO pop FROM mf.funds f JOIN mf.amcs a USING(amc_id)
        WHERE a.amfi_amc_name=spec->>'amc' AND f.in_scope;
    IF pop IS NULL OR jsonb_array_length(pop) NOT BETWEEN 1 AND (spec->>'maximum_current_variants')::integer
        OR NEW.document->>'population_hash' IS DISTINCT FROM encode(sha256(convert_to(mf_review.canonical_json(pop),'UTF8')),'hex') THEN
        RAISE EXCEPTION 'complete current in-scope AMC population required'; END IF;
    SELECT jsonb_agg(batch_id ORDER BY disclosure_as_of DESC,batch_id),count(*) INTO ids,n
        FROM mf_book_facts.batches WHERE source_key=NEW.source_key AND NEW.document->'batch_ids' ? batch_id;
    IF ids IS DISTINCT FROM NEW.document->'batch_ids' OR n<>(spec->>'period_count')::integer OR
        (SELECT count(DISTINCT disclosure_as_of) FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id)<>n THEN
        RAISE EXCEPTION 'two distinct complete ordered periods required'; END IF;
    FOR b IN SELECT * FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id LOOP
        IF (SELECT jsonb_agg(x->'identity' ORDER BY (x->'identity'->>'code')::integer) FROM jsonb_array_elements(b.document->'observations') x) IS DISTINCT FROM pop
            OR b.disclosure_as_of>NEW.observed_at::date OR b.document->>'specification_hash'<>NEW.document->>'specification_hash' THEN
            RAISE EXCEPTION 'batch current identity/specification differs'; END IF;
    END LOOP;
    IF (SELECT dataset_version::text FROM mf.dataset_summary WHERE singleton) IS DISTINCT FROM NEW.document->>'dataset_version' THEN
        RAISE EXCEPTION 'run live dataset receipt differs'; END IF;
    -- Catalog originals are retained byte-for-byte in the same append-only DB.
    IF NOT (jsonb_typeof(NEW.document->'catalog_hashes')='array' AND jsonb_array_length(NEW.document->'catalog_hashes') BETWEEN (spec->>'minimum_catalogue_originals')::integer AND 3
        AND (SELECT count(DISTINCT content_hash) FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.media_type IN('text/html','application/json') AND NEW.document->'catalog_hashes' ? s.content_hash)=jsonb_array_length(NEW.document->'catalog_hashes')) IS TRUE THEN
        RAISE EXCEPTION 'retained catalogue originals required'; END IF;
    SELECT count(DISTINCT content_hash) INTO n FROM (
        SELECT content_hash FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id
        UNION SELECT identity_content_hash FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id
    ) originals;
    IF jsonb_array_length(NEW.document->'archives') IS DISTINCT FROM n THEN RAISE EXCEPTION 'all PDF original archives required'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(NEW.document->'archives') LOOP
        IF NOT (item->>'uri'='gs://tlws_mf_data_source/'||(spec->>'archive_prefix')||(item->>'sha256')||'.pdf'
            AND item->>'generation' ~ '^[1-9][0-9]*$'
            AND EXISTS(SELECT 1 FROM mf_book_facts.sources s WHERE s.source_key=NEW.source_key AND s.content_hash=item->>'sha256' AND s.byte_length=(item->>'size')::integer)
        ) IS TRUE THEN RAISE EXCEPTION 'archive binding differs'; END IF;
    END LOOP;
    IF (SELECT count(DISTINCT x->>'sha256') FROM jsonb_array_elements(NEW.document->'archives') x WHERE x->>'sha256' IN(
        SELECT content_hash FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id
        UNION SELECT identity_content_hash FROM mf_book_facts.batches WHERE NEW.document->'batch_ids' ? batch_id))<>n THEN
        RAISE EXCEPTION 'distinct complete archived originals required'; END IF;
    IF (NEW.status='unchanged') IS DISTINCT FROM EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.source_key=NEW.source_key
        AND r.status<>'failed' AND r.document->'batch_ids'=NEW.document->'batch_ids') THEN RAISE EXCEPTION 'new versus replay status differs'; END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION mf_book_facts.seal_input() FROM PUBLIC;
COMMIT;
