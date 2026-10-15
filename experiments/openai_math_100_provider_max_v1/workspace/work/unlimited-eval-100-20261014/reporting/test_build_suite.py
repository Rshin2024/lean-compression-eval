import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import build_suite as suite
from test_comparison import fixture,MODELS


class Scripts(HTMLParser):
    def __init__(self):super().__init__();self.collect=False;self.scripts=[]
    def handle_starttag(self,tag,attrs):
        self.collect=tag=='script' and dict(attrs).get('type')!='application/json'
    def handle_data(self,text):
        if self.collect:self.scripts.append(text)
    def handle_endtag(self,tag):
        if tag=='script':self.collect=False


class SuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name);source=root/'fixture';fixture(source)
        for model in MODELS:
            p=source/('report-'+model)/'all_scheduled_attempts.json';rows=json.loads(p.read_text())
            for row in rows:row['generation']['generation_settings']['max_output_tokens']=128000
            p.write_text(json.dumps(rows))
        cls.data=suite.build(source,output=root/'out',classification='synthetic_test_fixture')
        cls.page=(root/'out/index.html').read_text()
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_complete_isolated_fixture_and_javascript(self):
        self.assertEqual(self.data['totalAttempts'],9600)
        self.assertEqual(self.data['generation']['outputTokenCap'],128000)
        page=suite.augment_page(self.page,self.data)
        self.assertEqual(page.count('Wall-timeout outcomes may depend on host speed.'),1)
        self.assertIn('terminal_stream_error',page)
        for name in ('operational-disclosure.json','transport-cost-disclosure.json','suite-build-receipt.json'):
            self.assertIn('href="'+name+'"',page)
        if shutil.which('node'):
            parser=Scripts();parser.feed(page)
            for index,script in enumerate(parser.scripts):
                p=Path(self.temp.name)/('script'+str(index)+'.js');p.write_text(script)
                result=subprocess.run(['node','--check',str(p)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
    def test_whitespace_and_body_closing_are_supported(self):
        page=self.page.replace('</html>','</body>\n</html>\n')
        self.assertIn('Wall-timeout outcomes',suite.augment_page(page,self.data))
    def test_metadata_may_contain_the_same_hardware_sentence(self):
        data={**self.data,'operationalDisclosure':{'hardware_limitations':'Wall-timeout outcomes may depend on host speed.'}}
        page=suite.augment_page(self.page,data)
        self.assertEqual(page.count('id="provider-max-hardware-note"'),1)
        self.assertEqual(page.count('Wall-timeout outcomes may depend on host speed.'),2)
    def test_missing_hardware_target_fails(self):
        with self.assertRaisesRegex(ValueError,'Checker-method'):
            suite.augment_page(self.page.replace('id="checkerMethods"','id="other"'),self.data)
    def test_duplicate_data_target_fails(self):
        with self.assertRaisesRegex(ValueError,'duplicated'):
            suite.augment_page(self.page+'<script id="data" type="application/json"></script>',self.data)


if __name__=='__main__':unittest.main()
