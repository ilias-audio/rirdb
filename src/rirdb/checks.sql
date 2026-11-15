-- Rules that span tables (data-model.md, section 10). Each query must return no rows.
-- The checks on single columns (allowed values, ranges, cells that are never empty) are generated from
-- columns.csv by checks.py, so they are written once. checks.py runs these on the release folder, with one
-- view per table: irs, bands, spaces, datasets, downloads, files, all_irs, readers.

-- check: ir_id is unique
SELECT ir_id, count(*) AS n FROM irs GROUP BY ir_id HAVING count(*) > 1;

-- check: every IR names a dataset and a space of the release
SELECT ir_id FROM irs
WHERE dataset NOT IN (SELECT dataset FROM datasets) OR space NOT IN (SELECT space FROM spaces);

-- check: every IR names a pinned download of its dataset
SELECT i.ir_id, i.download FROM irs i
LEFT JOIN downloads d ON d.dataset = i.dataset AND d.download = i.download
WHERE d.download IS NULL;

-- check: every IR names a pinned file whose role is ir
SELECT i.ir_id, i.file FROM irs i
LEFT JOIN files f ON f.dataset = i.dataset AND f.file = split_part(i.file, '|', 1)
WHERE f.file IS NULL OR f.role <> 'ir';

-- check: a non-commercial licence never reads as "commercial use allowed"
SELECT ir_id, licence FROM irs WHERE licence_conditions LIKE '%non_commercial%' AND use_commercial = 'yes';

-- check: a grade S row has no defect
SELECT ir_id FROM irs WHERE grade = 'S' AND (defects_major > 0 OR defects_minor > 0);

-- check: a row with a major defect has grade C or D (or none, in an anechoic space)
SELECT ir_id, grade FROM irs WHERE defects_major > 0 AND coalesce(grade, '') NOT IN ('C', 'D', '');

-- check: duplicate_of names an IR with the same audio_hash
SELECT a.ir_id FROM irs a LEFT JOIN irs b ON b.ir_id = a.duplicate_of
WHERE a.duplicate_of <> '' AND (b.ir_id IS NULL OR b.audio_hash <> a.audio_hash);

-- check: same_as names a space of the release
SELECT space, same_as FROM spaces WHERE same_as <> '' AND same_as NOT IN (SELECT space FROM spaces);

-- check: every space with rows has primary rows
SELECT s.space FROM spaces s
WHERE s.n_irs > 0 AND NOT EXISTS (SELECT 1 FROM irs i WHERE i.space = s.space AND i.is_primary);

-- check: the octave T30 columns of irs equal the octave rows of bands
SELECT i.ir_id, b.band_hz FROM irs i JOIN bands b ON b.ir_id = i.ir_id AND b.series = 'octave'
WHERE b.t30 IS DISTINCT FROM CASE b.band_hz
    WHEN 31.5 THEN i.t30_31 WHEN 63 THEN i.t30_63 WHEN 125 THEN i.t30_125 WHEN 250 THEN i.t30_250
    WHEN 500 THEN i.t30_500 WHEN 1000 THEN i.t30_1000 WHEN 2000 THEN i.t30_2000 WHEN 4000 THEN i.t30_4000
    WHEN 8000 THEN i.t30_8000 WHEN 16000 THEN i.t30_16000 END;

-- check: rt_broadband is the broadband row of bands (T30 where valid, else T20)
SELECT i.ir_id FROM irs i JOIN bands b ON b.ir_id = i.ir_id AND b.series = 'broadband'
WHERE i.rt_broadband IS DISTINCT FROM coalesce(b.t30, b.t20);

-- check: rt_mid is empty only when neither T30 nor T20 is valid in both mid octaves
SELECT ir_id FROM irs WHERE rt_mid IS NULL AND (t30_mid IS NOT NULL OR t20_mid IS NOT NULL);

-- check: t30_mid is empty only when T30 is not valid in the 500 Hz or the 1 kHz octave
SELECT ir_id FROM irs WHERE t30_mid IS NULL AND t30_500 IS NOT NULL AND t30_1000 IS NOT NULL;

-- check: every IR found in a dataset without a cap is in irs
SELECT dataset, n_irs, n_irs_found FROM datasets WHERE n_irs IS NOT NULL AND cap IS NULL AND n_irs <> n_irs_found;

-- check: a dataset with a cap lists every IR found, in all_irs, and its irs rows are the sampled ones
SELECT d.dataset FROM datasets d
WHERE d.cap IS NOT NULL AND d.n_irs IS NOT NULL
  AND (d.n_irs_found <> (SELECT count(*) FROM all_irs a WHERE a.dataset = d.dataset)
       OR d.n_irs <> (SELECT count(*) FROM all_irs a WHERE a.dataset = d.dataset AND a.sampled));

-- check: the irs rows of a dataset with a cap are its sampled rows of all_irs
SELECT i.ir_id FROM irs i JOIN datasets d USING (dataset)
WHERE d.cap IS NOT NULL AND i.ir_id NOT IN (SELECT ir_id FROM all_irs WHERE sampled);

-- check: every IR of a sampled space keeps at least one row (the cap draws per space, condition and layout)
SELECT a.dataset, a.space FROM all_irs a GROUP BY a.dataset, a.space HAVING NOT bool_or(a.sampled);

-- check: every row of irs has exactly one row of readers, and readers has no other row
SELECT coalesce(i.ir_id, r.ir_id) AS ir_id FROM irs i FULL OUTER JOIN readers r ON r.ir_id = i.ir_id
WHERE i.ir_id IS NULL OR r.ir_id IS NULL
UNION ALL
SELECT ir_id FROM readers GROUP BY ir_id HAVING count(*) > 1;

-- check: every measured dataset has its reader's settings
SELECT dataset FROM datasets WHERE n_irs IS NOT NULL AND (reader = '' OR reader_settings = '');

-- check: each space has one primary layout (data-model.md, section 3)
SELECT space, count(DISTINCT layout) AS layouts FROM irs WHERE is_primary GROUP BY space HAVING count(DISTINCT layout) > 1;
