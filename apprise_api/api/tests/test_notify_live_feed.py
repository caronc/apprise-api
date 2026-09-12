#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
# All rights reserved.
#
# This code is licensed under the MIT License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files(the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions :
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from django.test import SimpleTestCase

# This small DOM harness runs createNotifyLiveFeed() without a browser.
_HARNESS = r"""
function makeNode(tag) {
  return {
    tagName: tag,
    className: '',
    textContent: '',
    title: '',
    hidden: false,
    children: [],
    parentNode: null,
    setAttribute(name, value) { this[name] = value; },
    appendChild(node) {
      if (node.isFragment) {
        node.children.forEach((child) => this.appendChild(child));
        node.children = [];
        return node;
      }
      this.children.push(node);
      node.parentNode = this;
      return node;
    },
    replaceChildren() {
      this.children = [];
    },
    querySelector(sel) {
      const matches = (n) => {
        if (sel.startsWith('[') && sel.endsWith(']')) {
          return Object.prototype.hasOwnProperty.call(n, sel.slice(1, -1));
        }
        if (sel.startsWith('.')) {
          return (n.className || '').split(' ').includes(sel.slice(1));
        }
        return false;
      };
      const stack = [...this.children];
      while (stack.length) {
        const n = stack.shift();
        if (matches(n)) return n;
        stack.push(...n.children);
      }
      return null;
    },
    closest(sel) {
      let n = this;
      const cls = sel.startsWith('.') ? sel.slice(1) : sel;
      while (n) {
        if ((n.className || '').split(' ').includes(cls)) return n;
        n = n.parentNode;
      }
      return null;
    }
  };
}

function makePopupDom() {
  const container = makeNode('div');
  const list = makeNode('ul');
  list['data-notify-live-logs'] = true;
  container.appendChild(list);
  const empty = makeNode('p');
  empty['data-notify-live-empty'] = true;
  container.appendChild(empty);
  const count = makeNode('span');
  count['data-notify-live-count'] = true;
  container.appendChild(count);
  const panel = makeNode('div');
  panel.className = 'notify-log-panel';
  panel.appendChild(list);
  return {container, list, empty, count};
}

global.document = {
  createElement: (tag) => makeNode(tag),
  createDocumentFragment: () => {
    const fragment = makeNode('#fragment');
    fragment.isFragment = true;
    fragment.childNodes = fragment.children;
    return fragment;
  }
};
global.window = global;
let nextFrame = 1;
const frames = new Map();
global.requestAnimationFrame = (callback) => {
  const id = nextFrame++;
  frames.set(id, callback);
  return id;
};
global.cancelAnimationFrame = (id) => frames.delete(id);
function flushFrames() {
  const pending = Array.from(frames.values());
  frames.clear();
  pending.forEach((callback) => callback());
}

const live = makePopupDom();
const summary = makePopupDom();
let currentContainer = live.container;

global.Swal = {
  getHtmlContainer: () => currentContainer,
  getPopup: () => null,
  getConfirmButton: () => null,
  getCloseButton: () => null
};
global.appriseBeginTask = (options) => {
  options.didOpen();
  return {signal: {}, wasAborted: () => false, finish: () => {}, complete: () => {}};
};
global.appriseFire = (options) => {
  currentContainer = summary.container;
  options.didOpen();
};

eval(require('fs').readFileSync(process.argv[2], 'utf8'));

const feed = createNotifyLiveFeed('Title', 'Sending...');

feed.add({level: 'INFO', asctime: 't1', message: 'first'});
flushFrames();
const row1AfterFirst = live.list.children[0];
feed.add({level: 'WARNING', asctime: 't2', message: 'second'});
feed.add({level: 'ERROR', asctime: 't3', message: 'third'});
const lengthBeforeBatchRender = live.list.children.length;
flushFrames();
const row1AfterSecond = live.list.children[0];
const row1AfterThird = live.list.children[0];

const checks = [
  [row1AfterFirst === row1AfterSecond, 'row 1 was destroyed and recreated after the 2nd entry'],
  [row1AfterSecond === row1AfterThird, 'row 1 was destroyed and recreated after the 3rd entry'],
  [lengthBeforeBatchRender === 1, 'entries were rendered before the next animation frame'],
  [live.list.children.length === 3, 'expected exactly 3 rows in the live list, got ' + live.list.children.length],
  [live.count.textContent === '3 events', 'live event count text wrong: ' + live.count.textContent],
  [live.empty.hidden === true, 'the empty-state message should be hidden once rows exist']
];

feed.finish({tone: 'success', icon: 'success', summary: 'Done'});
checks.push([
  live.list.children.length === 3,
  'the completed popup should retain all 3 entries, got ' + live.list.children.length
]);
checks.push([
  live.list.children[0] === row1AfterFirst,
  'completing the popup replaced its existing rows'
]);
checks.push([
  summary.list.children.length === 0,
  'completing the feed opened a second popup'
]);

const failures = checks.filter(([ok]) => !ok).map(([, message]) => message);
if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}
process.exit(0);
"""


class NotifyLiveFeedRenderTests(SimpleTestCase):
    """Ensure streamed log entries append without rebuilding existing rows."""

    def _extract_create_notify_live_feed(self):
        """Extract the self-contained live-feed function for the Node harness."""
        from django.test import Client

        response = Client().get("/cfg/notify_live_feed_test_key", headers={"accept": "text/html"})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        marker = "function createNotifyLiveFeed"
        start = html.index(marker)
        open_brace = html.index("{", start)
        depth = 0
        i = open_brace
        while True:
            if html[i] == "{":
                depth += 1
            elif html[i] == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1
        return html[start:i]

    def test_live_feed_appends_without_rebuilding_rows(self):
        """Existing rows keep their identity as new entries arrive."""
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available to check JavaScript behavior")

        function_source = self._extract_create_notify_live_feed()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as handle:
            handle.write(function_source)
            function_path = handle.name

        try:
            result = subprocess.run(
                [node, "-e", _HARNESS, "harness", function_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            os.unlink(function_path)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_live_feed_has_stable_height_and_row_animation(self):
        """The live panel stays still while new rows animate into view."""
        from django.conf import settings

        stylesheet = Path(settings.BASE_DIR) / "static" / "css" / "base.css"
        content = stylesheet.read_text(encoding="utf-8")

        self.assertIn(".notify-live-feed .notify-log-panel", content)
        self.assertIn("height: clamp(8rem, 42dvh, 24rem);", content)
        self.assertIn("animation: notify-log-row-enter 180ms ease-out both;", content)
        self.assertIn("@keyframes notify-log-row-enter", content)
