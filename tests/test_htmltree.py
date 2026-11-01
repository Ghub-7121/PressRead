"""极简 HTML 树：检索、取文本、链接密度、script 原文保留。"""

import unittest

from pressread import htmltree

PAGE = """<html><head>
<title>Kelp | WIRED</title>
<meta name="author" content="Mara Ellison">
<meta property="og:title" content="Kelp Boom">
<script type="application/ld+json">{"@type":"NewsArticle","headline":"Kelp Boom"}</script>
<style>.a{color:red}</style>
</head>
<body>
<nav class="menu"><a href="/a">One</a><a href="/b">Two</a><a href="/c">Three</a></nav>
<article id="main">
  <h1>Kelp Boom</h1>
  <p class="lead">First <b>paragraph</b> with <a href="/x">a link</a>.</p>
  <p>Second paragraph.<br>After the break.</p>
</article>
<footer><p>© 2026</p></footer>
</body></html>"""


class ParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = htmltree.parse(PAGE)

    def test_text_and_own_text(self):
        paragraphs = self.tree.find_all("p")
        own = paragraphs[0].own_text()
        self.assertIn("First", own)
        self.assertNotIn("paragraph", own)  # 嵌套标签的文字不算 own_text
        self.assertIn("First paragraph with a link.", paragraphs[0].text())

    def test_br_becomes_separator(self):
        second = self.tree.find_all("p")[1]
        self.assertIn("paragraph. After the break.", second.text())

    def test_classes_and_id(self):
        lead = self.tree.first("p", cls="lead")
        self.assertIsNotNone(lead)
        self.assertTrue(lead.has_class("lead"))
        self.assertEqual(self.tree.first("article").id, "main")

    def test_ancestors(self):
        link = self.tree.find_all("a")[-1]
        tags = [node.tag for node in link.ancestors()]
        self.assertIn("p", tags)
        self.assertIn("article", tags)

    def test_link_density(self):
        nav = self.tree.first("nav")
        self.assertGreater(nav.link_density(), 0.9)
        lead = self.tree.first("p", cls="lead")
        self.assertLess(lead.link_density(), 0.3)

    def test_script_text_excluded_but_raw_kept(self):
        scripts = self.tree.find_all("script")
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0].own_text(), "")
        self.assertIn("Kelp Boom", scripts[0].raw)
        body_text = self.tree.first("article").text()
        self.assertNotIn("NewsArticle", body_text)
        self.assertNotIn("color:red", body_text)

    def test_meta_content(self):
        self.assertEqual(htmltree.meta_content(self.tree, name="author"), "Mara Ellison")
        self.assertEqual(htmltree.meta_content(self.tree, prop="og:title"), "Kelp Boom")
        self.assertEqual(htmltree.meta_content(self.tree, name="missing"), "")

    def test_find_all_limit(self):
        self.assertEqual(len(self.tree.find_all("p", limit=1)), 1)

    def test_void_tags_do_not_swallow_siblings(self):
        tree = htmltree.parse("<div><img src='a.png'><p>After image.</p></div>")
        self.assertEqual(tree.first("p").text(), "After image.")

    def test_malformed_html_survives(self):
        tree = htmltree.parse("<div><p>Unclosed <b>bold<p>Next")
        self.assertTrue(tree.find_all("p"))

    def test_html_to_text(self):
        text = htmltree.html_to_text("<p>Hello &amp; <b>bye</b></p><script>x</script>")
        self.assertEqual(text, "Hello & bye")


if __name__ == "__main__":
    unittest.main()
