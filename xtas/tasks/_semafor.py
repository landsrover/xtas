# Copyright 2013-2015 Netherlands eScience Center and University of Amsterdam
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Semafor semantic parser

This module assumes SEMAFOR_HOME to point to the location
where semafor is cloned/installed, and MALT_MODEL_DIR to
the location where the Malt models are downloaded.

The to_conll and add_frames method also convert 'Penn' style
trees to conll format, which requires CORENLP_HOME to point
to a CoreNLP installation dir.

This module runs semafor in 'interactive' mode, which is
not available in the stable SEMAFOR v2.1. Instead, install
from the latest source from

git clone https://github.com/Noahs-ARK/semafor

Note that this requires Maven 3 to build.

See: http://www.ark.cs.cmu.edu/SEMAFOR
See: http://nlp.stanford.edu/software/corenlp.shtml
"""

from __future__ import absolute_import

import datetime
import json
import os
import threading
import subprocess
import tempfile


class _Semafor(object):
    def __init__(self):
        self.start_semafor()

    def start_semafor(self):
        semafor_home = os.environ["SEMAFOR_HOME"]
        model_dir = os.environ.get("MALT_MODEL_DIR", semafor_home)
        cp = os.path.join(semafor_home, "target", "Semafor-3.0-alpha-04.jar")
        cmd = ["java", "-Xms4g", "-Xmx4g", "-cp", cp,
               "edu.cmu.cs.lti.ark.fn.SemaforInteractive",
               "model-dir:" + model_dir]
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE)
        for _ in self._read_next_output():
            pass

    def _read_next_output(self):
        while True:
            line = self.process.stdout.readline()
            if line == '':
                raise Exception("Unexpected EOF")
            if line.strip() == ">>>":
                break
            yield line

    def call_semafor(self, conll_str):
        self.process.stdin.write(conll_str.strip())
        self.process.stdin.write("\n\n")
        self.process.stdin.flush()
        lines = list(self._read_next_output())
        line, = lines   # Raises if len(lines) != 1.
        return json.loads(line)


_SINGLETON_LOCK = threading.Lock()


def call_semafor(conll_str):
    """
    Call semafor on the given conll_str using a thread-safe singleton instance
    """
    with _SINGLETON_LOCK:
        if not hasattr(_Semafor, 'singleton'):
            _Semafor.singleton = _Semafor()
        return _Semafor.singleton.call_semafor(conll_str)


def to_conll(tree):
    """
    Convert a parse tree from Penn (?) to conll
    """
    classpath = os.path.join(os.environ["CORENLP_HOME"], "*")
    javaclass = "edu.stanford.nlp.trees.EnglishGrammaticalStructure"
    # create stub xml file and call the conll class
    xml = ("<root><document><sentences><sentence>{tree}"
           "</sentence></sentences></document></root>"
           .format(**locals()))
    with tempfile.NamedTemporaryFile() as f:
        f.write(xml)
        f.flush()
        cmd = ['java', '-cp', classpath, javaclass, '-conllx',
               '-treeFile', f.name]
        return subprocess.check_output(cmd, shell=False)


def add_frames(saf_article):
    """
    Adds frames to a SAF-article, which should have a "trees" attribute
    with penn-style trees. The article will be modified in-place
    """
    saf_article['frames'] = []
    provenance = {'module': "semafor",
                  "started": datetime.datetime.now().isoformat()}
    saf_article['header']['processed'].append(provenance)

    for t in saf_article['trees']:
        sid = int(t['sentence'])
        tree = t['tree']
        conll = to_conll(tree)
        tokens = sorted((w for w in saf_article['tokens']
                         if w['sentence'] == sid),
                        key=lambda token: int(token['offset']))
        sent = call_semafor(conll)
        if "error" in sent:
            err = {"module": module, "sentence": sid}
            err.update(sent)
            saf_article.setdefault('errors', []).append(err)
            continue
        frames, sem_tokens = sent["frames"], sent["tokens"]
        assert len(tokens) == len(sem_tokens)

        def get_tokenids(f):
            for span in f["spans"]:
                for i in range(span["start"], span["end"]):
                    yield tokens[i]['id']

        for frame in frames:
            f = {"sentence": sid,
                 "name": frame["target"]["name"],
                 "target": list(get_tokenids(frame["target"])),
                 "elements": []}
            for a in frame["annotationSets"][0]["frameElements"]:
                f["elements"].append({"name": a["name"],
                                      "target": list(get_tokenids(a))})
            saf_article['frames'].append(f)

    return saf_article
