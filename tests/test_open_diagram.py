"""Run with: python3 -m unittest discover -s tests"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from open_diagram import (Diagram, Graph, LayoutError, MermaidError, Panel, SpecError, Theme, Tile,  # noqa: E402
                          from_mermaid, from_spec, text_width, wrap)
from open_diagram import cli  # noqa: E402
from open_diagram.fonts import face  # noqa: E402
from open_diagram.mermaid import parse  # noqa: E402
from open_diagram.spec import panel_from_spec  # noqa: E402

EXAMPLES = ["quickstart", "systems_map", "how_it_works", "pipeline", "card_payment", "layers", "concept_map",
            "smart_home", "incident_response", "business_map"]


def load_example(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "examples", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TextTests(unittest.TestCase):
    def test_width_grows_with_text(self):
        self.assertLess(text_width("ab", 12), text_width("abc", 12))

    def test_kerning_is_applied(self):
        f = face("regular")
        self.assertLess(f.kern(*f.names("AV")), 0)
        self.assertLess(text_width("AV", 100), text_width("A", 100) + text_width("V", 100))

    def test_wrap_respects_width(self):
        for line in wrap("one two three four five six seven", 12, 70):
            self.assertTrue(text_width(line, 12) <= 70 or " " not in line, line)

    def test_newline_forces_a_break(self):
        self.assertEqual(wrap("first\nsecond", 12, None), ["first", "second"])


class CheckTests(unittest.TestCase):
    def problems(self, d):
        return d.render()[1].problems

    def test_overlap_is_reported(self):
        d = Diagram("t")
        d.add(Panel("Alpha", ["x"]), 0, 0)
        d.add(Panel("Beta", ["y"]), 20, 20)
        self.assertTrue(any("'Alpha' and 'Beta' overlap" in p for p in self.problems(d)))

    def test_small_gap_is_reported(self):
        d = Diagram("t")
        a = d.add(Panel("Alpha", ["x"]))
        d.right_of(a, Panel("Beta", ["y"]), gap=5)
        self.assertTrue(any("only 5 px apart" in p for p in self.problems(d)))

    def test_clean_layout_has_no_problems(self):
        d = Diagram("t")
        a = d.add(Panel("Alpha", ["x"]))
        b = d.right_of(a, Panel("Beta", ["y"]))
        d.link(a, b)
        self.assertEqual(self.problems(d), [])

    def test_crossing_line_is_reported_unless_allowed(self):
        d = Diagram("t")
        a = d.add(Panel("Alpha", ["x"]))
        b = d.right_of(a, Panel("Beta", ["y"]), align="center")
        c = d.right_of(b, Panel("Gamma", ["z"]), align="center")
        line = d.link(a, c)
        self.assertTrue(any("crosses 'Beta'" in p for p in self.problems(d)))
        line.cross_ok = True
        self.assertEqual(self.problems(d), [])

    def test_lane_route_avoids_the_crossing(self):
        d = Diagram("t")
        a = d.add(Panel("Alpha", ["x"]))
        b = d.right_of(a, Panel("Beta", ["y"]), align="center")
        c = d.right_of(b, Panel("Gamma", ["z"]), align="center")
        d.link(a, c, start="top", end="top", via_y=a.y - 40)
        self.assertEqual(self.problems(d), [])

    def test_graph_label_collision_is_reported(self):
        g = Graph({"a": "A", "b": "B"}, [("a", "b", "a very long label for a short line")],
                  {"a": (0, 0), "b": (0.6, 0)})
        d = Diagram("t")
        d.add(Panel("Map", g))
        self.assertTrue(any("touches the node" in p for p in self.problems(d)))

    def test_unknown_status_names_the_choices(self):
        d = Diagram("t")
        d.add(Panel("Alpha", [Tile("x", status="nope")]))
        with self.assertRaisesRegex(ValueError, "live"):
            d.render()

    def test_save_raises_but_still_writes(self):
        d = Diagram("t")
        d.add(Panel("Alpha", ["x"]), 0, 0)
        d.add(Panel("Beta", ["y"]), 10, 10)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out")
            with self.assertRaises(LayoutError) as caught:
                d.save(path, png=False)
            self.assertTrue(os.path.exists(path + ".svg"))
            self.assertIn("overlap", str(caught.exception))


class ThemeTests(unittest.TestCase):
    def test_custom_colors_reach_the_picture(self):
        theme = Theme(tones={"default": "#123456"}, badge_fill="#ABCDEF")
        d = Diagram("t", theme=theme)
        d.add(Panel("Alpha", ["x"], step=1))
        svg, report = d.render()
        self.assertEqual(report.problems, [])
        self.assertIn("#123456", svg)
        self.assertIn("#ABCDEF", svg)

    def test_header_text_turns_white_on_dark_headers(self):
        t = Theme()
        self.assertEqual(t.header_colors("#2B3036"), (t.header_text_on_dark, t.header_sub_on_dark))
        self.assertEqual(t.header_colors("#E2E5E9"), (t.header_text, t.header_sub))

    def test_3d_look_can_be_turned_off(self):
        on, off = Theme(), Theme(lid=0, depth=0)
        self.assertEqual(Tile("x").measure(on)[0] - Tile("x").measure(off)[0], on.depth)
        self.assertEqual(Panel("A", ["x"]).measure(on)[1] - Panel("A", ["x"]).measure(off)[1], on.lid + on.depth)


class DescribeTests(unittest.TestCase):
    def test_describe_lists_blocks_states_and_lines(self):
        d = load_example("quickstart").build()
        text = d.describe()
        for part in ("How a search request is answered", "Web app: Search box; Results page; Filters",
                     "Caches results (being built)", "Search index [planned]", "Search API -> Search index",
                     "(connection to build)", 'labeled "new"'):
            self.assertIn(part, text)

    def test_description_is_written_into_the_svg(self):
        svg, _ = load_example("quickstart").build().render()
        self.assertIn("<desc>How a search request is answered", svg)


class StatusFileTests(unittest.TestCase):
    def test_set_statuses_by_name(self):
        d = load_example("quickstart").build()
        d.set_statuses({"Caches results": "live", "Product text": "retiring"})
        text = d.describe()
        self.assertIn("Checks access; Caches results. Owner", text)
        self.assertIn("Product text (retiring)", text)

    def test_unknown_names_and_states_are_reported(self):
        d = load_example("quickstart").build()
        with self.assertRaises(ValueError) as caught:
            d.set_statuses({"Nope": "live"})
        self.assertIn("'Nope'", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            d.set_statuses({"Filters": "shipped"})
        self.assertIn("'shipped'", str(caught.exception))


class SpecTests(unittest.TestCase):
    SPEC = {"title": "t", "columns": [[{"id": "a", "title": "Alpha", "tiles": ["x", {"label": "y", "status": "planned"}]}],
                                      [{"id": "b", "title": "Beta", "tiles": ["z"]}]],
            "links": [{"from": "a", "to": "b", "kind": "planned"}, {"from": "b", "to": "a", "lane": "bottom"}]}

    def test_spec_builds_cleanly(self):
        d = from_spec(self.SPEC)
        _, report = d.render()
        self.assertEqual(report.problems, [])
        self.assertEqual(len(d.links), 2)
        self.assertIn("y (planned)", d.describe())

    def test_spec_errors_name_the_place(self):
        bad = {**self.SPEC, "columns": [[{"id": "a", "title": "Alpha", "tiles": [{"label": "x", "colour": "red"}]}]]}
        with self.assertRaises(SpecError) as caught:
            from_spec(bad)
        self.assertIn("columns[0][0].tiles[0]", str(caught.exception))
        self.assertIn("'colour'", str(caught.exception))
        bad = {**self.SPEC, "links": [{"from": "a", "to": "c"}]}
        with self.assertRaises(SpecError) as caught:
            from_spec(bad)
        self.assertIn("no panel with id 'c'", str(caught.exception))

    def test_json_example_is_clean(self):
        _, report = cli.load(os.path.join(ROOT, "examples", "email_delivery.json")).render()
        self.assertEqual(report.problems, [])

    def test_card_and_graph_mistakes_name_the_place(self):
        # Issue #7: these raised a bare ValueError with no place, some only once the picture was measured.
        def section(sec):
            return {"title": "t", "columns": [[{"title": "P", "sections": [sec]}]]}

        graph = {"nodes": {"a": "A", "b": "B"}, "links": [["a", "b"]], "at": {"a": [0, 0], "b": [1, 0]}}
        cases = [({"graph": {**graph, "at": {"b": [1, 0]}}}, "graph.at: no position for node 'a'"),
                 ({"graph": {**graph, "links": [["a", "z"]]}}, "graph.links[0]: no node 'z'"),
                 ({"graph": {**graph, "at": {"a": 0, "b": [1, 0]}}}, "graph.at.a: expected [column, row]"),
                 ({"card": {"title": "C", "rows": [["only"]]}}, "card.rows[0]: each row is [name, value]"),
                 ({"card": {"title": "C", "rows": [["a", "b", "c"]]}}, "card.rows[0]: each row is [name, value]"),
                 ({"card": {"title": "C", "rows": [["Cost", 3]]}}, "card.rows[0]: the name and the value")]
        for sec, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(SpecError) as caught:
                    from_spec(section(sec))
                self.assertIn("columns[0][0].sections[0]." + message, str(caught.exception))
        with self.assertRaises(SpecError):
            panel_from_spec({"title": "P", "sections": [{"card": {"title": "C", "rows": [["only"]]}}]})
        self.assertEqual(from_spec(section({"graph": graph})).render()[1].problems, [])


class MermaidTests(unittest.TestCase):
    TEXT = """flowchart LR
      a[Start] --> b{Decide}
      b -->|yes| c[Do it]:::building
      b -- no --> a
      c -.-> d([Done])
      a --> d
      subgraph work [The work]
        c
      end
    """

    def test_parse_edges_labels_groups_and_states(self):
        d = from_mermaid(self.TEXT, title="Flow")
        self.assertEqual(d.title, "Flow")
        self.assertEqual(len(d.links), 5)
        kinds = {(l.a.name, l.b.name): l.kind for l in d.links}
        self.assertEqual(kinds[("Do it", "Done")], "planned")
        labels = {l.label for l in d.links if l.label}
        self.assertEqual(labels, {"yes", "no"})
        self.assertEqual([g.label for g in d.groups], ["The work"])
        self.assertIn("Do it (being built)", d.describe())

    def test_layout_is_clean_with_back_and_long_edges(self):
        for direction in ("LR", "RL", "TD", "BT"):
            with self.subTest(direction=direction):
                d = from_mermaid(self.TEXT.replace("LR", direction))
                _, report = d.render()
                self.assertEqual(report.problems, [])

    def test_mermaid_example_is_clean(self):
        _, report = cli.load(os.path.join(ROOT, "examples", "signup_flow.mmd")).render()
        self.assertEqual(report.problems, [])

    def test_node_names_that_match_mermaid_words(self):
        d = from_mermaid("flowchart LR\n  click[Click it] --> style[Style it]:::building\n  style -.-> class[Done]\n")
        self.assertEqual([(l.a.name, l.b.name, l.kind) for l in d.links],
                         [("Click it", "Style it", "flow"), ("Style it", "Done", "planned")])
        self.assertIn("Style it (being built)", d.describe())

    def test_top_down_with_a_group_is_clean(self):
        text = ("flowchart TD\n a[Ask] --> b{Found?}\n b -->|yes| c[Send link]\n b -->|no| d[Same message]\n"
                " c --> e[Click]\n e -.->|expired| a\n subgraph mail [Email service]\n  c\n end\n")
        for direction in ("TD", "BT"):
            with self.subTest(direction=direction):
                _, report = from_mermaid(text.replace("TD", direction)).render()
                self.assertEqual(report.problems, [])

    def test_right_to_left_and_bottom_to_top(self):
        # Issue #6: RL and BT were drawn exactly like LR and TD.
        start, end = from_mermaid("flowchart RL\n  a[Start] --> b[End]\n").placed
        self.assertGreater(start.x, end.right)
        d = from_mermaid("flowchart RL\n  a[Start] --> b[End]\n")
        self.assertEqual((d.links[0].start, d.links[0].end), ("left", "right"))
        d = from_mermaid("flowchart BT\n  a[Start] --> b[End]\n")
        start, end = d.placed
        self.assertGreater(start.y, end.bottom)
        self.assertEqual((d.links[0].start, d.links[0].end), ("top", "bottom"))
        for forward, reverse in (("LR", "RL"), ("TD", "BT")):
            with self.subTest(direction=reverse):
                self.assertNotEqual(from_mermaid(self.TEXT.replace("LR", forward)).render()[0],
                                    from_mermaid(self.TEXT.replace("LR", reverse)).render()[0])

    def test_errors_name_the_line(self):
        with self.assertRaises(MermaidError) as caught:
            from_mermaid("flowchart LR\n  a[ok] --> ???")
        self.assertIn("line 2", str(caught.exception))

    def test_separators_inside_quotes_are_part_of_the_text(self):
        # Mermaid keeps ; and %% inside a quoted label. Splitting the line on them first cut the statement in two,
        # so a line Mermaid reads was reported as unreadable.
        for text, text_of, label in (('flowchart LR\n  a["a;b"] --> b\n', "a;b", None),
                                     ('flowchart LR\n  a["100%% sure"] --> b\n', "100%% sure", None),
                                     ('flowchart LR\n  a -->|"x; y"| b\n', "a", "x; y"),
                                     ('flowchart LR\n  a -- "x; y" --> b\n', "a", "x; y"),
                                     ('flowchart LR\n  a["x] y"] --> b\n', "x] y", None)):
            with self.subTest(text=text):
                g = parse(text)
                self.assertEqual(g.order, ["a", "b"])
                self.assertEqual(g.text, {"a": text_of, "b": "b"})
                self.assertEqual([(e[0], e[1], e[2], e[3]) for e in g.edges], [("a", "b", "flow", label)])

    def test_separators_after_a_quoted_label_still_split_statements_and_comments(self):
        g = parse('flowchart LR\n  a["a;b"] --> b; b --> c %% not read; x --> y\n  c -->|"x; y"| d\n')
        self.assertEqual(g.order, ["a", "b", "c", "d"])
        self.assertEqual(g.text, {"a": "a;b", "b": "b", "c": "c", "d": "d"})
        self.assertEqual([(e[0], e[1], e[3]) for e in g.edges], [("a", "b", None), ("b", "c", None), ("c", "d", "x; y")])

    def test_subgraph_title_with_a_separator_in_quotes(self):
        g = parse('flowchart LR\n  subgraph s["a;b"]\n    x --> y\n  end\n  y --> z\n')
        self.assertEqual([g.groups[0][0], g.group_of], ["a;b", {"x": 0, "y": 0}])
        self.assertEqual([(e[0], e[1]) for e in g.edges], [("x", "y"), ("y", "z")])

    def test_separators_outside_quotes_are_unchanged(self):
        for text in ('flowchart LR\n  a --> b; c --> d\n', 'flowchart LR\n  a --- b; c --x d\n',
                     'flowchart LR\n  a ==> b; c <--> d\n'):
            with self.subTest(text=text):
                g = parse(text)
                self.assertEqual(g.order, ["a", "b", "c", "d"])
                self.assertEqual([(e[0], e[1]) for e in g.edges], [("a", "b"), ("c", "d")])


class GroupTitleTests(unittest.TestCase):
    def test_label_on_a_group_title_is_reported(self):
        d = Diagram("t")
        a = d.add(Panel("A", ["x"]), 0, 60)
        b = d.add(Panel("B", ["y"]), 420, 100)
        d.group("Group title", [b])
        d.link(a, b, start="right", end="left", via_y=78, label="a long label that reaches the group title")
        _, report = d.render()
        self.assertTrue(any("sits on the title of group 'Group title'" in p for p in report.problems))


class LayoutShapeTests(unittest.TestCase):
    def test_ring_places_blocks_apart_and_auto_sides_face_each_other(self):
        d = Diagram("t")
        around, middle = d.ring([Panel(f"Step {i}", ["x"]) for i in range(5)], center=[Panel("Goal", ["g"])])
        self.assertEqual((len(around), len(middle)), (5, 1))
        for a, b in zip(around, around[1:] + around[:1]):
            d.link(a, b, start="auto", end="auto")
        _, report = d.render()
        self.assertEqual(report.problems, [])

    def test_auto_picks_facing_sides(self):
        d = Diagram("t")
        a = d.add(Panel("Top", ["x"]), 0, 0)
        b = d.below(a, Panel("Below", ["y"]))
        line = d.link(a, b, start="auto", end="auto")
        d.render()
        self.assertEqual((line.start, line.end), ("bottom", "top"))

    def test_line_facing_away_is_reported(self):
        d = Diagram("t")
        a = d.add(Panel("Left", ["x"]), 0, 0)
        b = d.right_of(a, Panel("Right", ["y"]))
        d.link(a, b, start="left", end="left")
        _, report = d.render()
        self.assertTrue(any("leaves 'Left' from its left side, facing away from 'Right'" in p for p in report.problems))

    def test_json_panels_and_ring_layouts(self):
        free = {"title": "t", "panels": [{"id": "a", "title": "A", "tiles": ["x"], "x": 0, "y": 0},
                                         {"id": "b", "title": "B", "tiles": ["y"], "x": 320, "y": 160}],
                "links": [{"from": "a", "to": "b"}]}
        _, report = from_spec(free).render()
        self.assertEqual(report.problems, [])
        _, report = cli.load(os.path.join(ROOT, "examples", "release_cycle.json")).render()
        self.assertEqual(report.problems, [])

    def test_json_layout_errors(self):
        cases = [({"title": "t", "panels": [{"title": "A", "tiles": ["x"], "x": 0}]}, "needs numbers for x and y"),
                 ({"title": "t", "columns": [[{"title": "A", "x": 1}]]}, "only work in the 'panels' layout"),
                 ({"title": "t", "columns": [[{"title": "A"}]], "ring": [{"title": "B"}]}, "exactly one layout"),
                 ({"title": "t"}, "exactly one layout")]
        for spec, words in cases:
            with self.subTest(words=words):
                with self.assertRaises(SpecError) as caught:
                    from_spec(spec)
                self.assertIn(words, str(caught.exception))

    def test_public_spec_helpers(self):
        from open_diagram.spec import _panel, _theme, panel_from_spec, theme_from_spec
        self.assertIs(_panel, panel_from_spec)
        self.assertIs(_theme, theme_from_spec)
        self.assertEqual(panel_from_spec({"title": "A", "tiles": ["x"]}).title, "A")


class YellowTests(unittest.TestCase):
    def test_step_numbers_turn_dark_when_yellow_headers_are_used(self):
        d = Diagram("t")
        d.add(Panel("Plain", ["x"], step=1), 0, 0)
        self.assertEqual(d._drawing_theme().badge_fill, Theme().badge_fill)
        d.add(Panel("Decision", ["y"], tone="accent"), 300, 0)
        self.assertEqual(d._drawing_theme().badge_fill, Theme().tones["dark"])
        self.assertEqual(d.theme.badge_fill, Theme().badge_fill)    # the diagram's own theme is unchanged


class CommandTests(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_check_passes_and_fails(self):
        code, out = self.run_cli("check", os.path.join(ROOT, "examples", "quickstart.py"))
        self.assertEqual(code, 0)
        self.assertIn("0 layout problem(s)", out)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.py")
            with open(path, "w") as fh:
                fh.write("from open_diagram import Diagram, Panel\n"
                         "def build():\n    d = Diagram('t')\n    d.add(Panel('A', ['x']), 0, 0)\n"
                         "    d.add(Panel('B', ['y']), 5, 5)\n    return d\n")
            code, out = self.run_cli("check", path)
        self.assertEqual(code, 1)
        self.assertIn("overlap", out)

    def test_edited_script_is_read_fresh(self):
        # Same length, same timestamp: Python's bytecode cache would reuse the old version.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "edit.py")
            old = ("from open_diagram import Diagram, Panel\ndef build():\n    d = Diagram('t')\n"
                   "    a = d.add(Panel('A', ['x']), 0, 0)\n    d.add(Panel('B', ['y']), 60, 20)\n    return d\n")
            new = old.replace("d.add(Panel('B', ['y']), 60, 20)", "d.right_of(a, Panel('B', ['y']))")
            self.assertEqual(len(old), len(new))
            with open(path, "w") as fh:
                fh.write(old)
            stamp = os.stat(path).st_mtime
            self.assertEqual(self.run_cli("check", path)[0], 1)
            with open(path, "w") as fh:
                fh.write(new)
            os.utime(path, (stamp, stamp))
            self.assertEqual(self.run_cli("check", path)[0], 0)

    def test_draw_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_base = os.path.join(tmp, "flow")
            code, _ = self.run_cli("draw", os.path.join(ROOT, "examples", "signup_flow.mmd"), "-o", out_base,
                                   "--no-png")
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(out_base + ".svg"))

    def test_soundtrack(self):
        code, out = self.run_cli("--soundtrack")
        self.assertEqual(code, 0)
        self.assertIn("The Gathering Field", out)
        self.assertIn("open.spotify.com", out)


class RoutingTests(unittest.TestCase):
    """routing="orthogonal": every line is made of horizontal and vertical runs."""

    def assert_right_angles(self, line):
        self.assertNotIn("C", line.d)
        for a, b in zip(line.points, line.points[1:]):
            self.assertTrue(abs(a[0] - b[0]) < 0.01 or abs(a[1] - b[1]) < 0.01, line.points)

    def fan(self, routing="orthogonal"):
        d = Diagram("t", routing=routing)
        hub = d.add(Panel("Hub", ["One", "Two", "Three", "Four", "Five"]), 0, 520)
        targets = [d.add(Panel(f"Target {i}", ["x"]), 380, i * 130) for i in range(5)]
        sink = d.add(Panel("Sink", ["y"]), 760, 120)
        for t in targets:
            d.link(hub, t)
            d.link(t, sink)
        return d

    def test_every_line_has_right_angles(self):
        d = self.fan()
        top = d.add(Panel("Top", ["z"]), 1100, 0)
        d.link(d.placed[-2], top, start="right", end="bottom")     # an L shape
        d.link(d.placed[0], top, start="bottom", end="bottom")     # around the outside
        d.render()
        for line in d.links:
            self.assert_right_angles(line)

    def test_facing_blocks_get_a_straight_line(self):
        d = Diagram("t", routing="orthogonal")
        a = d.add(Panel("Alpha", ["x", "y", "z"]))
        b = d.right_of(a, Panel("Beta", ["w"]), align="center")
        line = d.link(a, b)
        self.assertEqual(d.render()[1].problems, [])
        self.assertEqual(len(line.points), 2)
        self.assertAlmostEqual(line.points[0][1], line.points[1][1])

    def test_lines_sharing_a_gap_get_their_own_tracks_and_do_not_cross(self):
        from open_diagram.diagram import _crossings
        d = self.fan()
        self.assertEqual(d.render()[1].problems, [])
        out = [l for l in d.links if l.a.name == "Hub" and len(l.points) == 4]
        self.assertGreaterEqual(len(out), 3)
        self.assertEqual(len({round(l.points[1][0], 3) for l in out}), len(out))
        self.assertEqual(sum(_crossings(p.points, q.points) for i, p in enumerate(d.links)
                             for q in d.links[i + 1:]), 0)

    def test_the_build_is_the_same_every_time(self):
        self.assertEqual(self.fan().render()[0], self.fan().render()[0])

    def test_one_link_can_differ_from_the_diagram(self):
        d = Diagram("t")
        a = d.add(Panel("Alpha", ["x"]))
        b = d.add(Panel("Beta", ["y"]), 300, 120)
        curved, square = d.link(a, b), d.link(a, b, routing="orthogonal")
        d.render()
        self.assertIn("C", curved.d)
        self.assert_right_angles(square)

    def test_crossings_are_still_reported(self):
        d = Diagram("t", routing="orthogonal")
        a = d.add(Panel("Alpha", ["x"]))
        b = d.right_of(a, Panel("Beta", ["y"]), align="center")
        c = d.right_of(b, Panel("Gamma", ["z"]), align="center")
        d.link(a, c)
        self.assertTrue(any("crosses 'Beta'" in p for p in d.render()[1].problems))

    def test_unknown_routing_is_refused(self):
        with self.assertRaises(ValueError):
            Diagram("t", routing="zigzag")
        d = Diagram("t")
        a, b = d.add(Panel("A", ["x"])), d.add(Panel("B", ["y"]), 300, 0)
        with self.assertRaises(ValueError):
            d.link(a, b, routing="zigzag")

    def test_json_routing(self):
        spec = {"title": "t", "routing": "orthogonal",
                "columns": [[{"id": "a", "title": "A", "tiles": ["x"]}],
                            [{"id": "b", "title": "B", "tiles": ["y"]}, {"id": "c", "title": "C", "tiles": ["z"]}]],
                "links": [{"from": "a", "to": "b"}, {"from": "a", "to": "c", "routing": "curved"}]}
        d = from_spec(spec)
        d.render()
        self.assert_right_angles(d.links[0])
        self.assertIn("C", d.links[1].d)
        with self.assertRaisesRegex(SpecError, "^routing:"):
            from_spec(dict(spec, routing="zigzag"))
        with self.assertRaisesRegex(SpecError, r"^links\[0\]:"):
            from_spec(dict(spec, routing="curved", links=[{"from": "a", "to": "b", "routing": "zigzag"}]))

    def test_mermaid_step_curve_means_right_angles(self):
        chart = "flowchart TD\n  A --> B & C\n"
        self.assertEqual(from_mermaid(chart).routing, "curved")
        self.assertEqual(from_mermaid('%%{init: {"flowchart": {"curve": "stepBefore"}}}%%\n' + chart).routing,
                         "orthogonal")
        self.assertEqual(from_mermaid("---\nconfig:\n  flowchart:\n    curve: step\n---\n" + chart).routing,
                         "orthogonal")
        self.assertEqual(from_mermaid(chart, routing="orthogonal").routing, "orthogonal")
        d = from_mermaid('%%{init: {"flowchart": {"curve": "step"}}}%%\n' + chart)
        self.assertEqual(d.render()[1].problems, [])
        for line in d.links:
            self.assert_right_angles(line)


class ExampleTests(unittest.TestCase):
    def test_examples_build_cleanly_and_repeatably(self):
        for name in EXAMPLES:
            with self.subTest(example=name):
                module = load_example(name)
                svg, report = module.build().render()
                self.assertEqual(report.problems, [])
                self.assertGreaterEqual(report.min_text, 10)
                self.assertEqual(svg, module.build().render()[0])


if __name__ == "__main__":
    unittest.main()
