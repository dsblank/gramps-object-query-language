#
# gramps-object-query-language - Object query language and SQL compiler for Gramps data
#
# Copyright (C) 2026      Douglas Blank
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#

"""A small random family tree full of awkward data, for `test_random_parity`.

The same seed always builds the same tree. Every kind of record is there,
and every field the query language reaches is sometimes filled in,
sometimes empty and sometimes missing: people with no birth, events with
no date, a text-only date or a range, empty and one-entry lists, families
with no father, private records and private entries inside them, strings
with `%`, `_`, quotes and digits, custom types next to built-in ones.
Strings are ASCII: `like(...)`'s case folding of other letters is a known,
documented SQLite/PostgreSQL difference, not something to rediscover.
"""

import random
from typing import Dict, List

from gramps.gen.db import DbTxn
from gramps.gen.lib import (
    Address,
    Attribute,
    AttributeType,
    ChildRef,
    ChildRefType,
    Citation,
    Date,
    Event,
    EventRef,
    EventRoleType,
    EventType,
    Family,
    FamilyRelType,
    Media,
    MediaRef,
    Name,
    NameType,
    Note,
    NoteType,
    Person,
    PersonRef,
    Place,
    PlaceName,
    PlaceRef,
    PlaceType,
    RepoRef,
    Repository,
    RepositoryType,
    Source,
    Surname,
    Tag,
    Url,
    UrlType,
)

GIVEN = ["John", "Jane", "Mary", "Robert", "Anna", "Steve", "123", "Al_ice", "Bob%", "", "O'Neil"]
SURNAMES = ["Smith", "Doyle", "Jones", "Anderson", "smith", "Mc_Kay", "100%", "", "Ng"]
WORDS = ["Chicago", "Boston", "accident", "Farmer", "paid 100%", "a_b", "Census Records",
         "", "x", "Died in a car accident.", "Line one\nline two", "C:\\path", "quote's"]
CUSTOM_TYPES = ["Tax list", "Handfasting", "Film", "Hair color", "Census"]


def _pick(rng: random.Random, values: List):
    return values[rng.randrange(len(values))]


def _maybe(rng: random.Random, p: float = 0.5) -> bool:
    return rng.random() < p


def _gramps_type(rng: random.Random, cls, builtins: List[int]):
    """A built-in type most of the time, otherwise a custom one by name."""
    value = cls()
    if _maybe(rng, 0.2):
        value.set(_pick(rng, CUSTOM_TYPES))
    else:
        value.set(_pick(rng, builtins))
    return value


def _date(rng: random.Random) -> Date:
    """A date of every shape: none, exact, partial, about/before/after, a
    range or span, text-only, estimated, BCE."""
    date = Date()
    kind = rng.randrange(10)
    year = rng.randrange(1700, 2020)
    if kind == 0:
        return date  # empty: sortval 0
    if kind == 1:
        date.set(text=_pick(rng, ["sometime", "unknown", "10%"]), modifier=Date.MOD_TEXTONLY)
        return date
    if kind in (2, 3):
        date.set(value=(rng.randrange(0, 29), rng.randrange(0, 13), year, False))
        return date
    if kind == 4:
        date.set(
            modifier=_pick(rng, [Date.MOD_ABOUT, Date.MOD_BEFORE, Date.MOD_AFTER]),
            value=(0, 0, year, False),
        )
        return date
    if kind == 5:
        date.set(
            modifier=_pick(rng, [Date.MOD_RANGE, Date.MOD_SPAN]),
            value=(1, 1, year, False, 1, 1, year + rng.randrange(0, 30), False),
        )
        return date
    if kind == 6:
        date.set(quality=Date.QUAL_ESTIMATED, value=(0, 0, year, False))
        return date
    if kind == 7:
        date.set(value=(0, 0, rng.randrange(1, 300), True))  # BCE
        return date
    date.set(value=(rng.randrange(1, 29), rng.randrange(1, 13), year, False))
    return date


def _attributes(rng: random.Random, obj, n_max: int = 3) -> None:
    for _ in range(rng.randrange(n_max + 1)):
        attr = Attribute()
        attr.set_type(
            _gramps_type(
                rng, AttributeType,
                [AttributeType.OCCUPATION, AttributeType.AGE, AttributeType.NICKNAME,
                 AttributeType.CAUSE],
            )
        )
        attr.set_value(_pick(rng, WORDS + ["42", "7"]))
        attr.set_privacy(_maybe(rng, 0.15))
        obj.add_attribute(attr)


def _name(rng: random.Random) -> Name:
    name = Name()
    name.set_first_name(_pick(rng, GIVEN))
    name.set_type(_gramps_type(rng, NameType, [NameType.BIRTH, NameType.MARRIED, NameType.AKA]))
    name.set_privacy(_maybe(rng, 0.1))
    surnames = []
    for i in range(_pick(rng, [0, 1, 1, 1, 2, 3])):
        surname = Surname()
        surname.set_surname(_pick(rng, SURNAMES))
        surname.set_primary(i == 0)
        if _maybe(rng, 0.2):
            surname.set_prefix(_pick(rng, ["van", "de", ""]))
        surnames.append(surname)
    name.set_surname_list(surnames)
    if _maybe(rng, 0.3):
        name.set_date_object(_date(rng))
    return name


def _links(rng: random.Random, handles: List[str], n_max: int) -> List[str]:
    if not handles:
        return []
    return rng.sample(handles, rng.randrange(min(n_max, len(handles)) + 1))


def build_random_tree(db, seed: int = 0, people: int = 40) -> Dict[str, List[str]]:
    """Fills `db` with a random tree; returns `{table: [handle, ...]}`."""
    rng = random.Random(seed)
    out: Dict[str, List[str]] = {}
    with DbTxn("random tree", db) as txn:
        tags = []
        for i in range(4):
            tag = Tag()
            tag.set_name(_pick(rng, ["ToDo", "Done", "a_b", "50%"]) + str(i))
            tag.set_color(_pick(rng, ["#000000", "#ff0000", ""]))
            tag.set_priority(rng.randrange(-1, 5))
            tags.append(db.add_tag(tag, txn))
        out["tag"] = tags

        def tagged(obj):
            for handle in _links(rng, tags, 2):
                obj.add_tag(handle)
            obj.set_privacy(_maybe(rng, 0.15))
            return obj

        notes = []
        for _ in range(14):
            note = Note()
            note.set(_pick(rng, WORDS))
            note.set_type(_gramps_type(rng, NoteType, [NoteType.GENERAL, NoteType.RESEARCH]))
            note.set_format(_pick(rng, [Note.FLOWED, Note.FORMATTED]))
            notes.append(db.add_note(tagged(note), txn))
        out["note"] = notes

        repos = []
        for _ in range(3):
            repo = Repository()
            repo.set_name(_pick(rng, WORDS))
            repo.set_type(_gramps_type(rng, RepositoryType, [RepositoryType.LIBRARY, RepositoryType.ARCHIVE]))
            for handle in _links(rng, notes, 1):
                repo.add_note(handle)
            repos.append(db.add_repository(tagged(repo), txn))
        out["repository"] = repos

        media = []
        for _ in range(4):
            medium = Media()
            medium.set_path(_pick(rng, ["a.jpg", "dir/b.png", "", "100%.gif"]))
            medium.set_description(_pick(rng, WORDS))
            medium.set_mime_type(_pick(rng, ["image/jpeg", "image/png", ""]))
            if _maybe(rng):
                medium.set_date_object(_date(rng))
            _attributes(rng, medium, 1)
            media.append(db.add_media(tagged(medium), txn))
        out["media"] = media

        def with_media(obj):
            for handle in _links(rng, media, 2):
                ref = MediaRef()
                ref.set_reference_handle(handle)
                ref.set_privacy(_maybe(rng, 0.15))
                obj.add_media_reference(ref)
            return obj

        sources = []
        for _ in range(5):
            source = Source()
            source.set_title(_pick(rng, WORDS))
            source.set_author(_pick(rng, GIVEN))
            source.set_abbreviation(_pick(rng, ["", "CR", "x_y"]))
            source.set_publication_info(_pick(rng, WORDS))
            _attributes(rng, source, 2)
            for handle in _links(rng, repos, 2):
                ref = RepoRef()
                ref.set_reference_handle(handle)
                ref.set_call_number(_pick(rng, ["", "A1", "B_2"]))
                source.add_repo_reference(ref)
            for handle in _links(rng, notes, 2):
                source.add_note(handle)
            sources.append(db.add_source(with_media(tagged(source)), txn))
        out["source"] = sources

        citations = []
        for _ in range(10):
            citation = Citation()
            if _maybe(rng, 0.85):
                citation.set_reference_handle(_pick(rng, sources))
            citation.set_page(_pick(rng, WORDS + ["p. 12", "12"]))
            citation.set_confidence_level(rng.randrange(0, 5))
            if _maybe(rng):
                citation.set_date_object(_date(rng))
            _attributes(rng, citation, 1)
            for handle in _links(rng, notes, 1):
                citation.add_note(handle)
            citations.append(db.add_citation(with_media(tagged(citation)), txn))
        out["citation"] = citations

        def cited(obj):
            for handle in _links(rng, citations, 2):
                obj.add_citation(handle)
            for handle in _links(rng, notes, 2):
                obj.add_note(handle)
            return obj

        places: List[str] = []
        for _ in range(10):
            place = Place()
            place.set_name(PlaceName(value=_pick(rng, WORDS)))
            place.set_title(_pick(rng, WORDS + ["Chicago, Cook, Illinois, USA"]))
            place.set_code(_pick(rng, ["", "60601", "IL"]))
            place.set_latitude(_pick(rng, ["", "41.88", "-12.5"]))
            place.set_longitude(_pick(rng, ["", "-87.62", "0"]))
            place.set_type(_gramps_type(rng, PlaceType, [PlaceType.CITY, PlaceType.COUNTY, PlaceType.COUNTRY]))
            for handle in _links(rng, places, 2):  # enclosed by earlier places: no cycles
                ref = PlaceRef()
                ref.set_reference_handle(handle)
                place.add_placeref(ref)
            if _maybe(rng, 0.3):
                place.add_alternative_name(PlaceName(value=_pick(rng, WORDS)))
            places.append(db.add_place(cited(tagged(place)), txn))
        out["place"] = places

        events = []
        for _ in range(people * 2):
            event = Event()
            event.set_type(
                _gramps_type(
                    rng, EventType,
                    [EventType.BIRTH, EventType.DEATH, EventType.MARRIAGE, EventType.CENSUS,
                     EventType.BURIAL, EventType.OCCUPATION],
                )
            )
            if _maybe(rng, 0.85):
                event.set_date_object(_date(rng))
            if _maybe(rng, 0.7):
                event.set_place_handle(_pick(rng, places))
            event.set_description(_pick(rng, WORDS))
            _attributes(rng, event, 1)
            events.append(db.add_event(with_media(cited(tagged(event))), txn))
        out["event"] = events

        def event_refs(obj, n_max):
            for handle in _links(rng, events, n_max):
                ref = EventRef()
                ref.set_reference_handle(handle)
                ref.set_role(_gramps_type(rng, EventRoleType, [EventRoleType.PRIMARY, EventRoleType.WITNESS]))
                ref.set_privacy(_maybe(rng, 0.1))
                obj.add_event_ref(ref)

        persons = []
        for _ in range(people):
            person = Person()
            person.set_gender(_pick(rng, [Person.MALE, Person.FEMALE, Person.UNKNOWN, Person.OTHER]))
            person.set_primary_name(_name(rng))
            for _ in range(_pick(rng, [0, 0, 1, 2])):
                person.add_alternate_name(_name(rng))
            event_refs(person, 4)
            refs = person.get_event_ref_list()
            if refs and _maybe(rng, 0.7):
                person.set_birth_ref(refs[0])
            if len(refs) > 1 and _maybe(rng, 0.5):
                person.set_death_ref(refs[-1])
            _attributes(rng, person)
            for _ in range(_pick(rng, [0, 0, 1])):
                url = Url()
                url.set_path(_pick(rng, ["http://x.org", ""]))
                url.set_description(_pick(rng, WORDS))
                url.set_type(_gramps_type(rng, UrlType, [UrlType.WEB_HOME, UrlType.EMAIL]))
                person.add_url(url)
            if _maybe(rng, 0.2):
                address = Address()
                address.set_city(_pick(rng, WORDS))
                person.add_address(address)
            persons.append(db.add_person(with_media(cited(tagged(person))), txn))
        out["person"] = persons

        for handle in persons:  # associations, now that everyone exists
            if _maybe(rng, 0.2):
                person = db.get_person_from_handle(handle)
                ref = PersonRef()
                ref.set_reference_handle(_pick(rng, persons))
                ref.set_relation(_pick(rng, ["Godfather", "Friend", ""]))
                person.add_person_ref(ref)
                db.commit_person(person, txn)

        families = []
        children_pool = list(persons)
        for _ in range(people // 3):
            family = Family()
            father = _pick(rng, persons) if _maybe(rng, 0.8) else None
            mother = _pick(rng, persons) if _maybe(rng, 0.8) else None
            if father:
                family.set_father_handle(father)
            if mother:
                family.set_mother_handle(mother)
            family.set_relationship(
                _gramps_type(rng, FamilyRelType, [FamilyRelType.MARRIED, FamilyRelType.UNMARRIED, FamilyRelType.UNKNOWN])
            )
            children = _links(rng, [h for h in children_pool if h not in (father, mother)], 4)
            for child in children:
                ref = ChildRef()
                ref.set_reference_handle(child)
                ref.set_father_relation(_pick(rng, [ChildRefType.BIRTH, ChildRefType.ADOPTED, ChildRefType.STEPCHILD]))
                ref.set_mother_relation(_pick(rng, [ChildRefType.BIRTH, ChildRefType.ADOPTED, ChildRefType.NONE]))
                ref.set_privacy(_maybe(rng, 0.1))
                family.add_child_ref(ref)
            event_refs(family, 2)
            _attributes(rng, family, 2)
            family_handle = db.add_family(with_media(cited(tagged(family))), txn)
            families.append(family_handle)
            for parent in (father, mother):
                if parent:
                    person = db.get_person_from_handle(parent)
                    person.add_family_handle(family_handle)
                    db.commit_person(person, txn)
            for child in children:
                person = db.get_person_from_handle(child)
                person.add_parent_family_handle(family_handle)
                db.commit_person(person, txn)
        out["family"] = families
    return out
