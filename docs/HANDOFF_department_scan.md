# Hand-off: department faculty pages the script could not read

For a session with a browser. Written 2026-09-27 by the session that built
`pipeline/scan_departments.py`; that module and its tests are committed, and this
note is the whole brief. Alex's policy decisions are quoted where they matter.

## What this is for

`scan_departments` reads each program's faculty listing to (a) find the current
rank of every roster person and (b) find tenure-line faculty on the page whom we
do not hold — new hires. It reached **61 of 120 programs**. The other **59** need a
browser: **32 stored URLs are dead** and **27 sites showed no listing** to a
script (usually a JavaScript directory, sometimes a listing two clicks deep).

Nothing from the scan has been applied to the roster yet. Applying is the last
step below, done once, after the browser pass, with Alex.

## Per program, two things

1. **Find the faculty listing page** for the *planning program* (not the whole
   college). Save its visible text to
   `build/dept_pages/<department_id>__<anything>.txt` with the **page URL on
   line 1** and the text from line 2. One file per page; several pages per
   program are fine. `get_page_text` output pasted straight in is what the
   parser expects — it keeps a name on one line and the title on the next.
2. **If the stored URL is dead, record the working one**: edit `url` for that
   `department_id` in `data/roster/department.csv`. Prefer the program's own
   page over the college's. The site publishes this link on the program page.

Do not fill in ranks or new hires by hand; the extractor does that from the
saved text, with the same guards as the crawl.

## Then

```bash
.venv/bin/python -m pipeline.scan_departments --text-dir build/dept_pages          # merges saved pages; writes build/department_scan_<date>.csv
```

Read the CSV with Alex before applying. The kinds that matter:

| kind | meaning | action |
|---|---|---|
| `promotion` | page shows a higher tenure-line rank than the roster | applied by `--apply` |
| `new_hire` | tenure-line name on the program's listing, unknown to us | applied by `--apply` (Alex, 2026-09-27: new hires go straight into the roster if tenure-track) |
| `new_hire?` | same, but the page lists more unknown names than ours — a college-wide directory | **not** applied; ignore unless the program is genuinely that large |
| `demotion?` | page shows a *lower* rank | not applied; a decision for Alex — five in the crawl looked real (a Scholar profile's loose "Professor" had promoted them) |
| `chair_title?` | a named chair; rank unreadable | not applied; look up by hand if wanted |
| `non_tenure_title?` | roster person shown as emeritus / lecturer / adjunct / director | not applied; may mean an appointment-type change |
| `possible_move` | a name matching someone we hold at *another* program | not applied; check |

```bash
.venv/bin/python -m pipeline.scan_departments --text-dir build/dept_pages --apply    # promotions + new_hire rows -> roster
.venv/bin/python -m pipeline.build_db && .venv/bin/python -m pipeline.build_site
.venv/bin/python -m pipeline.match_openalex          # give new people OpenAlex candidates
git add data/roster && git commit && git push          # CI deploys
```

New people have no PhD year, so the rank-review flag will not see them; the
Scholar discovery agent will search them on its next batch automatically.

## Dead URLs — 32 programs

| id | Program | Stored URL |
|---|---|---|
| 5 | Ball State University | https://www.bsu.edu/academics/collegesanddepartments/urban-planning |
| 9 | Clemson University | http://clemson.edu/caah/departments/city-and-regional-planning/ |
| 10 | Cleveland State University | http://csuohio.edu/urban/mupd/mupd |
| 11 | Columbia University | http://arch.columbia.edu/programs/10-m-s-urban-planning |
| 15 | Eastern Michigan University | https://www.emich.edu/geography-geology/programs/urban-regional-planning/index.php |
| 18 | Florida State University | http://coss.fsu.edu/durp/ |
| 20 | Georgia Tech | http://planning.gatech.edu/ |
| 40 | Pratt Institute | http://pratt.edu/academics/architecture/city-and-regional-planning/ |
| 44 | Rutgers University, School of Environmental & Biological Sciences | http://sebs.rutgers.edu/ |
| 46 | San Diego State University | https://spa.sdsu.edu/index.php/academic_programs/city_planning/cp-overview |
| 48 | Savannah State University | https://www.savannahstate.edu/graduate/master-degrees/degrees-grad-us.shtml |
| 52 | Texas A&M University | http://laup.arch.tamu.edu/ |
| 54 | The New School | http://newschool.edu/public-engagement/ms-urban-policy-analysis-management/ |
| 59 | UC Irvine | http://ppd.soceco.uci.edu/ |
| 61 | UCLA | http://luskin.ucla.edu/urban-planning |
| 63 | USC | http://priceschool.usc.edu/programs/masters/mpl/ |
| 70 | University of Calgary | http://evds.ucalgary.ca/content/master-planning-mplan |
| 72 | University of Colorado, Denver | http://ucdenver.edu/academics/colleges/ArchitecturePlanning/Academics/DegreePrograms/MURP/Pages/MURP.aspx |
| 80 | University of Iowa | http://urban.uiowa.edu/ |
| 82 | University of Louisville | http://supa.louisville.edu/ |
| 83 | University of Manitoba | http://umanitoba.ca/faculties/architecture/programs/cityplanning/index.html |
| 84 | University of Maryland | http://arch.umd.edu/ursp/urban-studies-and-planning |
| 86 | University of Massachusetts-Boston | https://environment.umb.edu/graduate-programs/urban-planning-and-community-development-ms |
| 90 | University of Missouri-Kansas City | http://info.umkc.edu/aupd/academic-programs/urban-planning-design/ |
| 91 | University of Nebraska | http://architecture.unl.edu/degree-programs/community-and-regional-planning |
| 100 | University of Saskatchewan | https://artsandscience.usask.ca/geography/undergraduates/regional-and-urban-planning.php |
| 105 | University of Texas, San Antonio | https://klesse.utsa.edu/architecture-planning/msurp.html |
| 109 | University of Virginia | http://arch.virginia.edu/urban-environmental-planning |
| 113 | University of Wisconsin, Milwaukee | http://uwm.edu/sarup/program/planning/ |
| 115 | Virginia Tech | http://uap.vt.edu/ |
| 118 | Western Washington University | https://huxley.wwu.edu/urban-planning-and-sustainable-development-program |
| 119 | Westfield State University | http://westfield.ma.edu/academics/geography-and-regional-planning-department |

## Reachable but no listing found — 27 programs

| id | Program | Stored URL |
|---|---|---|
| 2 | Appalachian State University | https://geo.appstate.edu/ |
| 3 | Arizona State University | http://geoplan.asu.edu/ |
| 6 | Cal Poly, Pomona | http://env.cpp.edu/urp/urp |
| 7 | Cal Poly, San Luis Obispo | http://planning.calpoly.edu/ |
| 13 | Dalhousie University | http://dal.ca/faculty/architecture-planning/school-of-planning.html |
| 19 | Georgetown University | https://scs.georgetown.edu/programs/356/master-of-professional-studies-urban-and-regional-planning/ |
| 22 | Hunter College | http://www.hunterurban.org/ |
| 28 | McGill University | http://mcgill.ca/urbanplanning/school-urban-planning |
| 29 | Miami University | https://miamioh.edu/cas/academics/departments/geography/academics/majors/urban-regional-planning-major/ |
| 30 | Michigan State University | http://spdc.msu.edu/programs/urban_and_regional_planning |
| 32 | Missouri State University | http://geosciences.missouristate.edu/ |
| 37 | Northern Arizona University | http://nau.edu/SBS/GPR/Degrees-Programs/Geography/ |
| 45 | Salisbury University | https://www.salisbury.edu/explore-academics/programs/undergraduate-degree-programs/majors/urban-regional-planning-major.aspx |
| 53 | Texas Southern University | http://bjmlspa.tsu.edu/ |
| 56 | Tufts University | http://ase.tufts.edu/uep/ |
| 57 | Tulane University | https://architecture.tulane.edu/academics/sustainable-urbanism |
| 65 | Universite de Montreal | http://urbanisme.umontreal.ca/ |
| 78 | University of Illinois, Chicago | http://upp.uic.edu/ |
| 85 | University of Massachusetts-Amherst | http://umass.edu/larp/home |
| 95 | University of Oklahoma | https://architecture.ou.edu/regional-city-planning/ |
| 98 | University of Puerto Rico | http://planificacion.uprrp.edu/ |
| 99 | University of Quebec in Montreal | https://etudier.uqam.ca/programme/maitrise-etudes-urbaines |
| 106 | University of Toledo | http://utoledo.edu/llss/geography/ |
| 107 | University of Toronto | http://geography.utoronto.ca/graduate-planning/ |
| 111 | University of Waterloo, Ontario | http://uwaterloo.ca/planning/ |
| 116 | Wayne State University | https://clas.wayne.edu/usp |
| 120 | York University | http://urst.sosc.laps.yorku.ca/ |

## Things the crawl taught, so the browser pass does not relearn them

- The **program's** listing, not the college's. Iowa State's design school page
  named 70 strangers beside our 10; the `new_hire?` guard exists for that, but a
  program-level page is better evidence.
- **Named chairs hide rank** ("Germeshausen Professor of Urban Planning"). The
  extractor marks them `chair_title?`; if the page also states the rank somewhere
  (a CV, a bio line), a note in the CSV's `snippet` is enough for Alex.
- **Laval-style pages call everyone "Professor"**. The extractor detects a page
  that gives no one assistant or associate and reads no rank from it.
- **News and headlines** mention names near the word "Professor". Only a short
  line that is essentially the name, with the title on it or the next line,
  counts as an entry.

## Where Alex's decisions live

- New hires straight into the roster if tenure-track: this conversation, 2026-09-27.
- Publish or Perish is deprecated; a small OpenAlex record is the person's figure: memory note `pop-deprecated-small-openalex-wins`.
- Dead department URLs to be fixed as part of this pass: this conversation, 2026-09-27.
