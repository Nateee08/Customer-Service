"""Fill the supplied QA PDF without replacing its original artwork."""
import base64,io,json
from pathlib import Path
from functools import lru_cache
from reportlab.pdfgen import canvas
from reportlab.lib.colors import Color
from pypdf import PdfReader,PdfWriter
ROOT=Path(__file__).parent
FIELDS=json.loads((ROOT/'qa-fields.json').read_text())

@lru_cache(maxsize=1)
def template():
 original=PdfReader(io.BytesIO(base64.b64decode((ROOT/'pdf-data/quality-assurance.b64').read_text())))
 page=original.pages[0];page.transfer_rotation_to_content()
 out=io.BytesIO();c=canvas.Canvas(out,pagesize=(612,792))
 for f in FIELDS:
  x=f['x']*612/1082;y=792-(f['y']+f['h'])*792/1400;w=f['w']*612/1082;h=f['h']*792/1400
  common=dict(name=f['name'],tooltip=f.get('label',f['name']),x=x,y=y,borderWidth=0,forceBorder=False,fillColor=Color(1,1,1),textColor=Color(0,0,0))
  if f['kind']=='radio':c.acroForm.radio(value=f['value'],selected=False,size=min(w,h),buttonStyle='check',shape='square',**common)
  elif f['kind']=='checkbox':c.acroForm.checkbox(checked=False,size=min(w,h),buttonStyle='check',**common)
  else:c.acroForm.textfield(width=w,height=h,fontSize=8,maxlen=3000 if f['kind']=='textarea' else 300,fieldFlags='multiline' if f['kind']=='textarea' else '',**common)
 c.showPage();c.save()
 writer=PdfWriter();writer.clone_document_from_reader(PdfReader(io.BytesIO(out.getvalue())))
 writer.pages[0].merge_page(page,over=False)
 result=io.BytesIO();writer.write(result);return result.getvalue()

def completed(values):
 writer=PdfWriter();writer.clone_document_from_reader(PdfReader(io.BytesIO(template())))
 clean={}
 for f in FIELDS:
  value=str(values.get(f['name'],'')).strip()
  if f['kind']=='checkbox':value='/Yes' if value in ('Yes','on','true') else '/Off'
  elif f['kind']=='radio':
   value='/'+value if value in ('Exceeds','Meets','Could do better') else '/Off'
  clean[f['name']]=value
 writer.update_page_form_field_values(None,clean,auto_regenerate=False)
 out=io.BytesIO();writer.write(out);return out.getvalue()
