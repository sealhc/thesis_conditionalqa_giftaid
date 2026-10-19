import json, unittest, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import pilot
class MatrixTests(unittest.TestCase):
 def test_check(self): pilot.check()
 def test_r0_no_synthetic_gold(self):
  gold=pilot.read('gold/gold_per_run.json')['scenario_statuses']
  self.assertNotIn('R0',gold)
 def test_no_c4(self):
  self.assertNotIn('G_C4', {x['id'] for x in pilot.read('gold/reference_conditions_and_logic.json')['atoms']})
 def test_pair_same_gold(self):
  g=pilot.read('gold/gold_per_run.json')['scenario_statuses']
  for k in ('R1','R2','R3'):
   self.assertEqual(g[k+'_L']['statuses'],g[k+'_H']['statuses'])
 def test_a3_receives_original(self):
  m=pilot.read('model_inputs/R1_H.json')
  text=pilot.format_a3(m,{'atoms':[]},{'statuses':[]})
  self.assertIn(m['customer_scenario'],text)
  self.assertIn(m['decision_question'],text)
  self.assertIn('INTERMEDIATE_REPRESENTATION',text)
  self.assertIn('CONDITION_STATUSES',text)
 def test_r0_not_scored_as_nei(self):
  self.assertIsNone(pilot.read('gold/gold_per_run.json')['scenario_statuses'].get('R0'))

 def test_gold_validator(self):
  import subprocess
  r=subprocess.run([sys.executable,'-I',str(ROOT/'checks/validate_lowhigh.py')],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
 def test_thinking_default_off(self):
  src=(ROOT/'scripts/ollama_run.py').read_text(encoding='utf8')
  self.assertIn('think = bool(args.think)',src)
  self.assertNotIn('--no-think',src)
if __name__=='__main__':unittest.main()
