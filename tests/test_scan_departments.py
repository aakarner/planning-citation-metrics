"""Reading a faculty listing page: titles for people we hold, and new hires.

Precision over recall throughout. A missed rank costs a reviewer a minute; a
lecturer read as a professor, or a stranger read as a new hire, puts a wrong
fact on the site.
"""

from pipeline.scan_departments import (classify_title, find_person, known_person, names_with_tenure_titles,
                                       rank_on_page, text_of)

PAGE = """<html><body><h1>Faculty</h1>
<div class="card"><h3>Ada Lovelace</h3><p>Associate Professor</p><p>ada@somewhere.edu</p></div>
<div class="card"><h3>Grace Hopper</h3><p>Professor and Chair</p></div>
<div class="card"><h3>Mary Somerville</h3><p>Assistant Professor of Practice</p></div>
<div class="card"><h3>Emmy Noether</h3><p>Assistant Professor</p></div>
<div class="card"><h3>Rosalind Franklin</h3><p>Senior Lecturer</p></div>
<div class="card"><h3>Barbara McClintock</h3><p>Professor Emerita</p></div>
<p>Read More About Our Program</p>
</body></html>"""
TEXT = text_of(PAGE)


def test_titles_are_classified_and_non_tenure_ones_get_no_rank():
    assert classify_title("Associate Professor") == ("associate", "associate professor")
    assert classify_title("Professor and Chair") == ("full", "professor")
    assert classify_title("Assistant Professor of Practice") == (None, "assistant professor")
    assert classify_title("Professor Emerita") == (None, "professor")
    assert classify_title("Senior Lecturer") == (None, "senior lecturer")
    assert classify_title("Clinical Associate Professor") == (None, "associate professor")
    assert classify_title("Nothing here") == (None, None)


def test_a_roster_person_is_found_in_several_name_orders():
    assert find_person("Faculty: Lovelace, Ada -- Associate Professor", "Ada Lovelace", "Lovelace") >= 0
    assert find_person("Ada K. Lovelace, Associate Professor", "Ada Lovelace", "Lovelace") >= 0
    assert find_person("Ada Lovelace", "Ada Lovelace", "Lovelace") == 0
    assert find_person("Nobody here", "Ada Lovelace", "Lovelace") == -1


def test_rank_on_page_reads_the_title_next_to_the_name():
    assert rank_on_page(TEXT, "Ada Lovelace", "Lovelace")[:2] == ("associate", "associate professor")
    assert rank_on_page(TEXT, "Grace Hopper", "Hopper")[:2] == ("full", "professor")
    assert rank_on_page(TEXT, "Mary Somerville", "Somerville")[:2] == (None, "assistant professor")   # of practice
    assert rank_on_page(TEXT, "Nobody Atall", "Atall") == (None, None, "")


def test_new_hire_extraction_keeps_only_tenure_line_names():
    found = dict(names_with_tenure_titles(TEXT))
    assert found == {"Ada Lovelace": "associate", "Grace Hopper": "full", "Emmy Noether": "assistant"}
    # of-practice, lecturer and emerita are not tenure-line; the "Read More" line is not a name


def test_known_person_matches_variants_and_not_strangers():
    people = [{"person_id": "1", "first_name": "Ada", "last_name": "Lovelace", "display_name": "Ada Lovelace"},
              {"person_id": "2", "first_name": "Grace", "last_name": "Hopper", "display_name": "Grace Hopper"}]
    assert known_person("Ada K. Lovelace", people)["person_id"] == "1"
    assert known_person("A. Lovelace", people)["person_id"] == "1"
    assert known_person("Grace Murray Hopper", people)["person_id"] == "2"
    assert known_person("Emmy Noether", people) is None
    assert known_person("Zoe Lovelace", people) is None           # same surname, different first name
    assert known_person("Ada Byron", people) is None              # same first name, different surname


def test_abbreviations_honorifics_and_chairs():
    assert classify_title("Leah M. Hollstein , Assoc Professor") == ("associate", "associate professor")
    assert classify_title("Asst. Prof. of Planning") == ("assistant", "assistant professor")
    assert classify_title("Daniel Rose Professor of Urban Economics") == (None, "named chair")
    assert classify_title("Class of 1958 Career Development Professor") == (None, "named chair")
    assert classify_title("Ada Lovelace, Professor of Urban Planning") == ("full", "professor")   # the person's own name precedes
    assert classify_title("Professor and Chair") == ("full", "professor")
    assert dict(names_with_tenure_titles(text_of("<p>Dr. Edmund Merem</p><p>Professor</p>"))) == {"Edmund Merem": "full"}


def test_a_news_paragraph_is_not_a_listing_entry():
    news = text_of("<p>Elmond Bandauko was congratulated for receiving a $100,000 grant for his research on housing "
                   "in Harare, presented by the Professor of Planning at the ceremony.</p>"
                   "<h3>Elmond Bandauko</h3><p>Assistant Professor</p>")
    assert rank_on_page(news, "Elmond Bandauko", "Bandauko")[:2] == ("assistant", "assistant professor")
    prose_only = text_of("<p>Andi Binet publishes a reflection on working with the Professor of Practice programme.</p>")
    assert rank_on_page(prose_only, "Andi Binet", "Binet")[0] is None


def test_junk_lines_are_not_names():
    page = text_of("<h3>Curriculum Vitae</h3><p>Professor</p><h3>SPOTLIGHT STORIES</h3><p>Professor</p>"
                   "<h3>IDEA Center</h3><p>Associate Professor</p><h3>Meet Dr. Rachel Berney</h3><p>Assistant Professor</p>"
                   "<h3>Emmy Noether</h3><p>Assistant Professor</p>")
    assert dict(names_with_tenure_titles(page)) == {"Emmy Noether": "assistant"}


def test_a_single_word_named_chair_is_not_a_rank():
    assert classify_title("Mariana Arcaya Germeshausen Professor of Urban Planning") == (None, "named chair")
    assert classify_title("Germeshausen Professor of Urban Planning") == (None, "named chair")
    assert classify_title("Justin Steil Professor of Law and Urban Planning") == (None, "named chair")   # unpunctuated name reads the same; a comma is what listings use
    assert classify_title("Moises Gonzales, Professor CRP") == ("full", "professor")
    assert classify_title("Professor of Urban Planning") == ("full", "professor")


def test_a_headline_about_someone_is_not_their_entry():
    page = text_of("<h2>Magdalena Novoa’s Work with Chilean Women Featured at Krannert Art Museum</h2>"
                   "<p>Professor of Planning Jane Doe introduced the exhibit.</p>")
    assert rank_on_page(page, "Magdalena Novoa", "Novoa")[0] is None


def test_known_person_handles_apostrophes_middle_names_and_short_forms():
    people = [{"person_id": "1", "first_name": "Catherine", "last_name": "D\u2019Ignazio", "display_name": "Catherine D\u2019Ignazio"},
              {"person_id": "2", "first_name": "Phillip", "last_name": "Thompson", "display_name": "J. Phillip Thompson"},
              {"person_id": "3", "first_name": "Leonora", "last_name": "Angeles", "display_name": "Leonora Angeles"}]
    assert known_person("Catherine D'Ignazio", people)["person_id"] == "1"
    assert known_person("J. Phillip Thompson", people)["person_id"] == "2"
    assert known_person("Phillip Thompson", people)["person_id"] == "2"
    assert known_person("Nora Angeles", people)["person_id"] == "3"
    assert known_person("Jake Lewandowski", people) is None


def test_a_lower_rank_on_the_page_is_a_demotion_not_a_question():
    # the kind name is what --apply ranks acts on; the department page outranks a Scholar profile
    import pipeline.scan_departments as sd
    src = open(sd.__file__).read()
    assert '"demotion" if rank else' in src and '"demotion?"' not in src
    assert 'r["kind"] in ("promotion", "demotion")' in src
