from pathlib import Path
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
from nbconvert import HTMLExporter
import hashlib,json,sys,re,datetime
ROOT=Path(__file__).resolve().parents[1]
path=ROOT/'analysis.ipynb';nb=nbformat.read(path,as_version=4)
km=KernelManager(kernel_name='python3');km.kernel_spec.argv[0]=sys.executable
client=NotebookClient(nb,km=km,timeout=240,resources={'metadata':{'path':str(ROOT)}})
client.execute(cleanup_kc=True,cwd=str(ROOT))
nb.metadata['kernelspec']={'display_name':'Python 3','language':'python','name':'python3'}
nbformat.validate(nb);nbformat.write(nb,path)
images=sum('image/png' in output.get('data',{}) for cell in nb.cells if cell.cell_type=='code' for output in cell.get('outputs',[]))
errors=[output for cell in nb.cells if cell.cell_type=='code' for output in cell.get('outputs',[]) if output.output_type=='error'];assert not errors and images==4,(len(errors),images)
exporter=HTMLExporter(template_name='basic',exclude_input_prompt=True,exclude_output_prompt=True,embed_images=True,mathjax_url='')
body,_=exporter.from_notebook_node(nb)
assert not re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://',body,re.I)
html='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Lean compression — executed analysis companion</title><style>
body{margin:0;background:#f3f5f5;color:#172a34;font:16px/1.6 system-ui,-apple-system,sans-serif}article{max-width:1040px;margin:32px auto;padding:36px 44px;background:white;border:1px solid #dfe5e7;border-radius:12px}h1{font-size:32px;line-height:1.2}h2{margin-top:38px;font-size:24px}h3{margin-top:28px;font-size:19px}pre{font-size:12px;line-height:1.5;overflow-x:auto;padding:14px;background:#f2f5f6;border-radius:6px}code{font-family:ui-monospace,SFMono-Regular,monospace}img{display:block;max-width:100%;height:auto;margin:18px auto}table{border-collapse:collapse;font-size:13px;margin:16px 0;max-width:100%;display:block;overflow-x:auto}th,td{padding:7px 10px;border-bottom:1px solid #dbe2e6;text-align:right}th:first-child,td:first-child{text-align:left}.prompt{display:none}.output_subarea{overflow-x:auto}.input_area{margin-top:16px}.cell{margin-bottom:18px}a{color:#126b83}@media(max-width:720px){article{margin:0;padding:20px 16px;border:0;border-radius:0}h1{font-size:27px}pre{font-size:11px}}@media print{body{background:white}article{border:0;margin:0;padding:0}.input{break-inside:avoid}img{break-inside:avoid}}
</style></head><body><article>'''+body+'</article></body></html>'
(ROOT/'analysis.html').write_text(html)
checks=json.loads((ROOT/'reproducibility/notebook-checks.json').read_text())
receipt={'status':'executed_top_to_bottom','at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'python':sys.version,'cells':len(nb.cells),'code_cells':sum(c.cell_type=='code' for c in nb.cells),'execution_counts':[c.execution_count for c in nb.cells if c.cell_type=='code'],'error_outputs':len(errors),'embedded_png_figures':images,'html_external_script_or_stylesheet_dependencies':False,'notebook_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'html_sha256':hashlib.sha256((ROOT/'analysis.html').read_bytes()).hexdigest(),'checks':checks,'browser_html_rendered_qa':False,'rendered_qa_note':'Figure PNG inspection and root preview review are recorded separately; execution and export alone do not establish rendered acceptance.'}
(ROOT/'reproducibility/notebook-execution.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
