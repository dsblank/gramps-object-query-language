"""Error messages say what's wrong in the user's own words.

Every bad query below must fail with a message that quotes what was
written, never Python's syntax tree (`Name(id=...)`) or the parser's
rewritten form (`_pick(...)`, `_entry`, `je.value`). Some clients show
only the text before the first " -- ", so that part must say what's wrong
on its own; the cases with an expected message check that part.
"""

import pytest

from gramps_object_query_language.query import QueryError
from gramps_object_query_language.query_lang import (
    QueryLangError,
    compile_expr,
    parse_order_by,
    parse_select,
    resolve_namespace,
)

#: Text that means a message leaked internals.
_LEAKS = (
    "Name(", "Attribute(", "Call(", "Constant(", "Compare(", "Subscript(", "ListComp(",
    "_pick(", "_count(", "_distinct(", "_chain(", "_entry", "je.value", "link.value",
    "JsonPath(", "RelatedObject(", "Collection(", "ctx=Load",
)

#: (namespace, where_expr, the first " -- " part of its message, or None
#: to only check it fails cleanly).
CASES = [
    # Syntax.
    ("Person", "surname = 'Smith'", "invalid syntax at column 9, a single = isn't a comparison; write =="),
    ("Person", "surname ==", "invalid syntax, it ends too early; something is missing at the end"),
    ("Person", "", "invalid syntax: the expression is empty"),
    ("Person", "surname == 'Smith", None),
    # Names: near misses are suggested; flat columns count as fields.
    ("Person", "surnam == 'x'", "unknown field 'surnam' on Person (did you mean 'surname'?)"),
    ("Person", "birth.dat.sortval > 0", "unknown field 'dat' on Event (did you mean 'date'?)"),
    ("Person", "gender == Persn.MALE", "unknown name 'Persn' (did you mean 'Person'?)"),
    ("Person", "Person.MAL == gender", "Person has no MAL (did you mean 'MALE'?); its values: Person.FEMALE, Person.MALE, Person.OTHER, Person.UNKNOWN"),
    # A collection or link used as a field.
    ("Person", "parent_families.father.surname == 'x'", "parent_families is a collection of Family records, not a field"),
    ("Person", "child_refs[0].frel.value == 1", "child_refs is a collection, not a field, and its entries can't be picked by number"),
    ("Event", "event_refs[0].role.value == 1", "event_refs is a collection, not a field"),
    ("Person", "backlinks[0]._class == 'x'", "backlinks is a collection, not a field"),
    ("Person", "birth == None", "birth is a link to an Event record, not a value on its own"),
    ("Person", "events", "'events' is one of Person's collections, not a condition on its own"),
    ("Person", "surname", "'surname' is a value, not a condition"),
    # Things GOQL doesn't have.
    ("Person", "f(surname)", "f() isn't a GOQL function (its functions: any, len, like, regex, Date, distinct, exists, count)"),
    ("Person", "surname.upper() == 'X'", "surname.upper() calls a method, and GOQL has none; use a plain comparison"),
    ("Person", "all(e for e in events)", "all() isn't a GOQL function; use not any(x for x in <list> if not <condition>)"),
    ("Person", "sum([1 for e in events]) > 0", "sum() isn't a GOQL function; use len(...) to count; there are no other totals"),
    ("Person", "surname == f'x'", "f'x': f-strings aren't supported"),
    ("Person", "surname == {1: 2}", "{1: 2} isn't a value GOQL understands"),
    # Values.
    ("Person", "gramps_id == 3", "cannot compare gramps_id with 3: it holds text, so compare it with text in quotes ('Smith')"),
    ("Family", "len(children) > 'x'", "cannot compare this count with 'x': it holds a number, so compare it with a number (3)"),
    ("Person", "private == 'yes'", "cannot compare private with 'yes': it holds True or False, so compare it with True or False"),
    ("Person", "birth.date > 5", "birth.date is an object, so it can't be put in order"),
    ("Person", "Date('notadate') < birth.date.sortval", "Date('notadate'): not a date Gramps can read"),
    ("Person", "Date(1900) < birth.date.sortval", "Date(...) takes the date as text in quotes, e.g. Date('1900-01-31') or Date('about 1850')"),
    # Functions' arguments.
    ("Person", "like('x', surname)", "like()'s first argument is the field to match, e.g. like(surname, ...)"),
    ("Person", "like(surname, 3)", "like()'s pattern is text in quotes, e.g. like(surname, 'Sm%')"),
    ("Person", "any()", "any() takes a list or collection and, optionally, a condition: any(events), any(events, type == 'Census'), any(e for e in events if ...)"),
    ("Person", "len(1, 2, 3) > 0", None),
    ("Person", "len(events)", "len(events) is a number, not a condition"),
    ("Person", "len([c for f in parent_families for c in f.children])", "len([c for f in parent_families for c in f.children]) is a number, not a condition"),
    ("Person", "len(distinct([c.handle for f in parent_families for c in f.children]))", "len(distinct([c.handle for f in parent_families for c in f.children])) is a number, not a condition"),
    ("Person", "not len([c for f in parent_families if f.private == True for c in f.children])", "len([c for f in parent_families if f.private == True for c in f.children]) is a number, not a condition"),
    ("Person", "distinct([c.handle for f in parent_families for c in f.children])", "expected a condition"),
    ("Person", "any(surname)", "surname isn't a list, so it can't be counted or tested with any(...)"),
    ("Person", "any(note_list, x == 1)", "note_list holds plain values, with no fields for a condition to test"),
    ("Person", "exists(father.events)", None),
    # Loops and picks.
    ("Person", "any(e for e in events for f in families)", None),
    ("Person", "any(a.value == 'x' for a in f.father.attribute_list)", None),
    ("Person", "[e for e in events][0] == 1", "an entry of events is an Event record, not a value on its own"),
    ("Person", "[e for e in events][-1].gramps_id == 'x'", None),
    ("Person", "surname[-1] == 'x'", "surname[-1]: an index is a number from 0, e.g. [0] for the first"),
    ("Person", "len([n.surname_list[0].surname for n in alternate_names]) > 1", "n.surname_list[0].surname: a field to count can't go through an index ([0]) yet"),
    ("Person", "[e for e in events]", "[e for e in events] is a list, not a condition"),
    # The quoted text is what the user wrote, not the rewritten form.
    ("Person", "attribute_list['Film'].value", "\"attribute_list['Film'].value\" is a value, not a condition"),
    ("Person", "any(b.surname == 'x' for b in backlinks)", "b.surname == 'x': plain backlinks only knows each referrer's kind (_class == 'Person')"),
    ("Person", "primary_name['x'] == 1", None),
    ("Person", "Env.HOME == handle", "unknown Env value Env.HOME: no Env values were passed in with this query"),
]


def _message(namespace, expr):
    with pytest.raises((QueryLangError, QueryError)) as error:
        compile_expr(namespace, expr)
    return str(error.value)


@pytest.mark.parametrize("namespace, expr, expected", CASES, ids=[c[1] or "empty" for c in CASES])
def test_message(namespace, expr, expected):
    message = _message(namespace, expr)
    for leak in _LEAKS:
        assert leak not in message, f"{leak!r} leaked into: {message}"
    if expected is not None:
        assert message.split(" -- ")[0].split("\n")[0].startswith(expected), message


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("surnam", "invalid select entry 'surnam': unknown field 'surnam' on Person (did you mean 'surname'?)"),
        ("events", "invalid select entry 'events': events is a collection of Event records, not a field"),
        ("[e for e in events][0]", "invalid select entry '[e for e in events][0]': an entry of events is an Event record"),
        ("surname ==", "could not parse select entry, it ends too early"),
    ],
)
def test_select_message(entry, expected):
    with pytest.raises(QueryLangError) as error:
        parse_select(resolve_namespace("Person"), [entry])
    assert str(error.value).startswith(expected), str(error.value)


def test_order_by_message_quotes_the_entry():
    with pytest.raises(QueryLangError, match=r"^invalid order_by entry 'surnam': unknown field 'surnam' on Person \(did you mean 'surname'\?\)"):
        parse_order_by(resolve_namespace("Person"), ["surnam"])


def test_ordering_an_object_is_refused_but_equality_is_not():
    """`<` on a whole date ordered its JSON text in SQLite and crashed the
    evaluator; `==`/`!=` agreed on every engine and still compile."""
    with pytest.raises(QueryError, match="compare birth.date.sortval"):
        compile_expr("Person", "birth.date < 5")
    compile_expr("Person", "birth.date == None")
    compile_expr("Person", "birth.date != 5")
