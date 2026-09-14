-- Derived views. Everything here is recomputed on every build; nothing is stored.
-- These replace the seven VBA macros and the 'Top Faculty' and 'Current Schools'
-- sheets in the handover workbook.

-- Current primary regular appointment per person. Non-regular appointments
-- stay in the table but never enter rankings.
CREATE VIEW v_current_affiliation AS
SELECT a.*
FROM affiliation a
WHERE a.end_date IS NULL AND a.is_primary = 1 AND a.appointment_type = 'regular';

-- Most recent snapshot per person per source.
CREATE VIEW v_latest_metrics AS
SELECT m.*
FROM metric_snapshot m
JOIN (
  SELECT person_id, source, MAX(collected_at) AS collected_at
  FROM metric_snapshot
  GROUP BY person_id, source
) latest USING (person_id, source, collected_at);

-- The headline number for a person. Policy (decided 2026-09-14):
--   1. Google Scholar when the person has a profile;
--   2. otherwise OpenAlex, shown with its source and a note that it is
--      likely an undercount relative to Scholar (an incentive to create a
--      Scholar profile);
--   3. Publish or Perish and manual entries only while no OpenAlex snapshot
--      exists yet (the migrated 2026 numbers for the 243 non-profile faculty).
-- The `source` column travels with every headline number so the site can flag it.
CREATE VIEW v_headline_metrics AS
SELECT snapshot_id, run_id, person_id, source, collected_at,
       total_citations, h_index, i10_index, citations_5yr, h_index_5yr, works_count,
       source <> 'google_scholar' AS is_fallback
FROM (
  SELECT m.*,
         ROW_NUMBER() OVER (
           PARTITION BY m.person_id
           ORDER BY CASE m.source
                      WHEN 'google_scholar' THEN 1
                      WHEN 'openalex'       THEN 2
                      WHEN 'pop'            THEN 3
                      WHEN 'manual'         THEN 4
                      ELSE 9 END,
                    m.collected_at DESC
         ) AS rn
  FROM v_latest_metrics m
)
WHERE rn = 1;

-- OpenAlex for everyone who is matched, as a comparison series alongside Scholar.
CREATE VIEW v_openalex_metrics AS
SELECT * FROM v_latest_metrics WHERE source = 'openalex';

-- Percentiles among current faculty, overall and within rank.
-- PERCENT_RANK() = (rank - 1) / (n - 1), which matches Excel's PERCENTRANK.INC
-- for values present in the array.
CREATE VIEW v_person_percentiles AS
SELECT p.person_id,
       p.display_name,
       ca.department_id,
       d.short_name AS department,
       ca.rank,
       m.source,
       m.is_fallback,
       m.collected_at,
       m.total_citations,
       m.h_index,
       PERCENT_RANK() OVER (ORDER BY m.total_citations)                        AS pct_citations_all,
       PERCENT_RANK() OVER (PARTITION BY ca.rank ORDER BY m.total_citations)   AS pct_citations_rank,
       PERCENT_RANK() OVER (ORDER BY m.h_index)                                AS pct_h_all,
       PERCENT_RANK() OVER (PARTITION BY ca.rank ORDER BY m.h_index)           AS pct_h_rank,
       CAST(substr(m.collected_at, 1, 4) AS INTEGER) - p.phd_year              AS years_since_phd,
       CASE WHEN CAST(substr(m.collected_at, 1, 4) AS INTEGER) - p.phd_year > 0
            THEN m.total_citations * 1.0
                 / (CAST(substr(m.collected_at, 1, 4) AS INTEGER) - p.phd_year)
       END                                                                     AS citations_per_year
FROM person p
JOIN v_current_affiliation ca ON ca.person_id = p.person_id
JOIN department d             ON d.department_id = ca.department_id
JOIN v_headline_metrics m     ON m.person_id = p.person_id;

-- Ranked faculty list. Reproduces the 'Top Faculty' sheet.
CREATE VIEW v_top_faculty AS
SELECT RANK() OVER (ORDER BY total_citations DESC) AS rank_by_citations,
       RANK() OVER (ORDER BY h_index DESC)         AS rank_by_h_index,
       person_id, display_name, department, rank, total_citations, h_index,
       pct_citations_all, pct_citations_rank, pct_h_all, pct_h_rank
FROM v_person_percentiles;

-- Department summary. Reproduces the 'Current Schools' sheet and adds a few.
CREATE VIEW v_department_summary AS
WITH ranked AS (
  SELECT department_id, total_citations, h_index,
         ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY total_citations) AS rn_c,
         ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY h_index)         AS rn_h,
         COUNT(*)     OVER (PARTITION BY department_id)                          AS n
  FROM v_person_percentiles
),
med_c AS (
  SELECT department_id, AVG(total_citations) AS median_citations
  FROM ranked WHERE rn_c IN ((n + 1) / 2, (n + 2) / 2) GROUP BY department_id
),
med_h AS (
  SELECT department_id, AVG(h_index) AS median_h_index
  FROM ranked WHERE rn_h IN ((n + 1) / 2, (n + 2) / 2) GROUP BY department_id
),
agg AS (
  SELECT pp.department_id,
         COUNT(*)                                            AS n_faculty,
         SUM(pp.total_citations)                             AS total_citations,
         AVG(pp.total_citations)                             AS mean_citations,
         AVG(pp.h_index)                                     AS mean_h_index,
         SUM(CASE WHEN pp.source = 'google_scholar' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
                                                             AS share_with_scholar_profile
  FROM v_person_percentiles pp
  GROUP BY pp.department_id
)
SELECT d.department_id, d.short_name, d.university, d.country, d.url,
       agg.n_faculty, agg.total_citations, med_c.median_citations, agg.mean_citations,
       agg.total_citations * 1.0 / agg.n_faculty AS citations_per_faculty,
       med_h.median_h_index, agg.mean_h_index, agg.share_with_scholar_profile,
       RANK() OVER (ORDER BY med_c.median_citations DESC) AS rank_by_median_citations
FROM department d
JOIN agg   ON agg.department_id   = d.department_id
JOIN med_c ON med_c.department_id = d.department_id
JOIN med_h ON med_h.department_id = d.department_id;

-- Every observation for every person, for sparklines and trend lines.
CREATE VIEW v_person_timeseries AS
SELECT m.person_id, p.display_name, m.source, m.collected_at,
       m.total_citations, m.h_index, m.i10_index, m.citations_5yr
FROM metric_snapshot m
JOIN person p ON p.person_id = m.person_id
ORDER BY m.person_id, m.source, m.collected_at;
