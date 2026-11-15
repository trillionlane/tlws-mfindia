-- Owner-applied only, after 045. Preserve the existing dataset and grants.
ALTER TABLE mf_nse.sch_rows
    DROP CONSTRAINT sch_rows_original_cells_check,
    ADD CONSTRAINT sch_rows_original_cells_check
        CHECK (jsonb_array_length(original_cells) IN (43, 44));
