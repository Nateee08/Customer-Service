"""Rebuild the four supplied documents with straight, selectable text and aligned fields.
Original PDFs are retained under pdf-data/originals. Published Documents PDFs have no widgets.
"""
import base64,io,json,re
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor,white
from reportlab.lib.utils import ImageReader
from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
pdfmetrics.registerFont(TTFont('CleanSans','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('CleanSans-Bold','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'output/pdf';OUT.mkdir(parents=True,exist_ok=True)
ink=HexColor('#182c34');muted=HexColor('#58656b');line=HexColor('#cdd5d9');light=HexColor('#f2f5f6')
svg=(ROOT/'static/brand.svg').read_text()
im=Image.open(io.BytesIO(base64.b64decode(re.search('base64,([^\"]+)',svg).group(1))))
# The website's existing logo artwork, with transparent outer space removed.
box=im.getbbox();logo=im.crop(box);logo_reader=ImageReader(logo)
style=ParagraphStyle('body',fontName='CleanSans',fontSize=10.5,leading=14.8,textColor=ink,spaceAfter=10)
small=ParagraphStyle('small',parent=style,fontSize=8.8,leading=11.5,spaceAfter=7)
bold=ParagraphStyle('heading',parent=style,fontName='CleanSans-Bold',fontSize=11,leading=14,spaceBefore=8,spaceAfter=8)

def logo_at(c,x,y,w):
 h=w*logo.height/logo.width;c.drawImage(logo_reader,x,y,width=w,height=h,mask='auto')

def frame(c,doc):
 c.setFillColor(ink);logo_at(c,206,725,200)
 c.setStrokeColor(line);c.setLineWidth(.6);c.line(42,716,570,716)
 c.setFont('CleanSans',8);c.setFillColor(muted);c.drawString(42,25,'Hooked on Solar  |  Customer Care');c.drawRightString(570,25,str(doc.page))

def para(text,st=style):return Paragraph(escape(text).replace('\n','<br/>'),st)
def title(text):return Paragraph(escape(text),ParagraphStyle('title',parent=style,fontName='CleanSans-Bold',fontSize=22,leading=26,spaceAfter=18))
def build(name,story):
 SimpleDocTemplate(str(OUT/name),pagesize=(612,792),leftMargin=42,rightMargin=42,topMargin=83,bottomMargin=44).build(story,onFirstPage=frame,onLaterPages=frame)

def read(name):return (ROOT/'document-copy'/name).read_text().strip()
mission='We are committed to simplifying our customers’ lives by providing responsive, reliable, and professional service with a focus on trust, quality, and long-term relationships.'
# All policy text is transcribed from the supplied PDFs; only typographic errors corrected.
s=read('service-protocol-1.txt');start=s.index('Our team');end=s.index('You can cancel')
service=[title('2026 Service Protocol'),para('OUR MISSION',bold),para(mission),para(' '.join(s[start:end].split())),para('You can cancel a call 24 hours in advance.'),para('Service rates',bold)]
rates=Table([[para('1 Hour Service Call',bold),para('$380.00',bold)],[para('Labor - 1 hour'),para('$205.00')],[para('Labor - 1/2 hour'),para('$102.50')],[para('Trip charge on all calls'),para('$175.00')]],colWidths=[390,138])
rates.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),light),('LINEBELOW',(0,0),(-1,-1),.5,line),('LEFTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),5)]))
service.extend([rates,Spacer(1,10),para('Billed in 1/2 hour increments.'),para(' '.join(s[s.index('While not all'):].split()))]);build('service-protocol.pdf',service)

s=read('workmanship-warranty-1.txt');s=s[s.index('Hooked on Solar warrants'):s.index('If you believe warranty')]
s=s.replace('acknolwedges','acknowledges').replace('condequential','consequential').replace('sevice','service')
warranty=[title('1-Year Limited Workmanship Warranty')]
for block in s.split('\n\n'):
 block=' '.join(block.split())
 if block in ('Equipment Warranty:','Exclusions:','Limitations of Liability:'):warranty.append(para(block,bold))
 else:
  # OCR joins some bullets; preserve each original exclusion as a separate paragraph.
  pieces=re.split(r'(?:^|\s)[«¢©°*•]\s+',block)
  for text in pieces:
   if text.strip():warranty.append(para(text.strip(),small))
warranty.extend([Spacer(1,5),para('If you believe warranty service is needed, please contact:',small),para('Hooked On Solar | 2571 Warren Dr. Rocklin, CA 95677',small),para('customerservice@hookedonsolar.com | 855-466-5332 | CSLB # 949707',small)])
build('workmanship-warranty.pdf',warranty)

s1=read('subscription-plan-1.txt');s2=read('subscription-plan-2.txt')
sub=[title('Subscription Plan'),para('60 Day Introductory Offer',bold)]
plans=Table([[para('Basic $75.00 / Annual',bold),para('Gold $175 / Annual',bold)],[para('Priority Scheduling\n5% Discount on Service Invoices'),para('Priority Scheduling\n15% Discount on invoices\n30 minutes of Phone or Back Office Support billed in 15 minute increments')]],colWidths=[264,264])
plans.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),light),('BOX',(0,0),(-1,-1),.6,line),('INNERGRID',(0,0),(-1,-1),.5,line),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),8)]))
sub.extend([plans,Spacer(1,10),para('OUR MISSION',bold),para(mission),para('Why Subscription-Based Support Is Essential in Today’s Economic Climate',bold)])
s1=s1[s1.index('In today’s economic'):];s1=s1[:s1.rfind('\n\nG')].replace('obtaina','obtain an').replace('obtain a\nexcellent','obtain an\nexcellent')
for block in s1.split('\n\n'):sub.append(para(' '.join(block.split())))
sub.extend([PageBreak(),title('Subscription Plan Details')])
s2=s2[:s2.rfind('\n\n(')].replace('Asubscription','A subscription')
for block in s2.split('\n\n'):sub.append(para(' '.join(block.split()),small))
build('subscription-plan.pdf',sub)

# Clean QA page: identical business fields, rebuilt with a shared coordinate definition.
c=canvas.Canvas(str(OUT/'quality-assurance.pdf'),pagesize=(612,792));fields=[]
def text(x,top,t,size=9,b=False):
 c.setFillColor(ink);c.setFont('CleanSans-Bold' if b else 'CleanSans',size);c.drawString(x,792-top-size,t)
def rule(top):c.setStrokeColor(line);c.setLineWidth(.6);c.line(36,792-top,576,792-top)
def section(top,label):
 c.setFillColor(light);c.rect(36,792-top-18,540,18,fill=1,stroke=0);text(43,top+4,label,9,True)
def field(name,x,top,w,h=18,kind='text',label=None,required=False,value=None):
 f=dict(name=name,x=x*1082/612,y=top*1400/792,w=w*1082/612,h=h*1400/792,kind=kind)
 if label:f['label']=label
 if required:f['required']=True
 if value:f['value']=value
 fields.append(f)
 c.setStrokeColor(line);c.setLineWidth(.5);c.setFillColor(white);c.rect(x,792-top-h,w,h,fill=1,stroke=1)
logo_at(c,201,721,210);text(36,72,'Quality Assurance',22,True)
text(36,103,'855-466-5332 ext. 3  |  customerservice@hookedonsolar.com',9)
rule(122)
text(36,130,'Client signature (full name)',8,True);field('customerName',36,143,366,20,label='Client signature / customer full name',required=True)
text(420,130,'Date (MM/DD/YYYY)',8,True);field('signatureDate',420,143,156,20,required=True)
section(176,'EXPECTATION SCORE')
for name,label,top in [('Service technician','Service technician score',205),('Service department','Service department score',229)]:
 text(43,top,label,9,True)
 for x,value in [(247,'Exceeds'),(343,'Meets'),(429,'Could do better')]:
  field(name,x,top,10,10,'radio',label+': '+value,True,value);text(x+16,top-1,value,9)
text(36,254,'Our mission',9,True)
for i,t in enumerate(['We are committed to simplifying our customers’ lives by providing responsive, reliable, and','professional service with a focus on trust, quality, and long-term relationships.']):text(36,268+i*12,t,9)
for name,label,top in [('Notified on the way','Service technician notified you they were on their way.',299),('Problem identified','Service technician identified the problem.',317),('Problem fixed or explained','Service technician fixed the problem or explained how it will be resolved.',335),('Safety check','4 Seconds to Safety: surrounding location checked for safety.',353)]:
 field(name,36,top,10,10,'checkbox');text(53,top-1,label,9)
section(374,'SERVICE DETAILS')
text(36,399,'Job number',8,True);field('Job number',36,411,138)
text(190,399,'Service date (MM/DD/YYYY)',8,True);field('serviceDate',190,411,140,required=True)
text(347,399,'Discipline',8,True)
for x,label in [(347,'Solar'),(419,'Battery'),(504,'Generac')]:field('discipline_'+label,x,415,10,10,'checkbox',label);text(x+14,414,label,8)
text(36,440,'Service technician name',8,True);field('Service technician name',36,452,240)
text(292,440,'Completion date',8,True);field('Completion date',292,452,140)
field('Reschedule',449,457,10,10,'checkbox');text(464,456,'Reschedule',9)
section(484,'PUNCH LIST ITEMS / CLIENT SUGGESTIONS')
text(36,509,'Item / suggestion',8,True);text(315,509,'Name',8,True);text(420,509,'Minutes',8,True);text(475,509,'Completion date',8,True)
for i in range(1,5):
 top=523+(i-1)*24;text(36,top+4,str(i)+'.',8)
 field('Punch list '+str(i),50,top,253,22,'textarea');field(f'Item {i} name',315,top,93,22);field(f'Item {i} minutes',420,top,43,22);field(f'Item {i} completion',475,top,101,22)
section(627,'NOTES ON JOB')
field('Parts available',36,651,10,10,'checkbox');text(52,650,'Technician had all parts on the service vehicle; no second trip required.',8)
field('Other service issue',36,667,10,10,'checkbox');text(52,666,'Other service issue discovered on site.',8)
field('notes',36,683,540,26,'textarea','Notes on job')
text(36,717,'Other required completion items (utility, HOA, etc.)',8,True);text(355,717,'Scheduled completion',8,True);text(514,717,'Initials',8,True)
for i in range(1,4):
 top=730+(i-1)*15;field(f'Other completion item {i}',36,top,307,14);field(f'Scheduled completion {i}',355,top,147,14);field(f'Initials {i}',514,top,62,14)
c.showPage();c.save()
(ROOT/'qa-fields.json').write_text(json.dumps(fields,indent=2))
for name in ['service-protocol','workmanship-warranty','subscription-plan','quality-assurance']:
 (ROOT/'pdf-data'/f'{name}.b64').write_text(base64.b64encode((OUT/f'{name}.pdf').read_bytes()).decode())
