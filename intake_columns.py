"""Move legacy website columns only after durable backup and read-back verification."""

def values(row):
 return {str(cell['columnId']):cell.get('value') for cell in row.get('cells',[])}

def complete(sheet):
 rows=sheet.get('rows')
 if not isinstance(rows,list) or len(rows)<sheet.get('totalRowCount',len(rows)):
  raise ValueError('Incomplete sheet; legacy columns retained')
 return rows

def migrate(api,backup,on_verified=lambda count:None):
 sheet=api('GET','',params={'includeAll':'true'})
 rows=complete(sheet)
 cols={c['title'].strip().casefold():c for c in sheet.get('columns',[])}
 job=next((cols[k] for k in ('job #','job#','job number','job no','project number') if k in cols),None)
 pairs=[]
 for old,new in [('website ticket #',job),('website subject',cols.get('issue'))]:
  if old in cols:
   if not new:raise ValueError('Destination column missing')
   pairs.append((cols[old],new))
 if not pairs:return {'moved':0,'removed':0}
 backup({'columns':[c for pair in pairs for c in pair], 'rows':[{'id':r['id'],'cells':[c for c in r.get('cells',[]) if str(c['columnId']) in {str(col['id']) for pair in pairs for col in pair}]} for r in rows]})
 updates=[]
 for row in rows:
  cells=values(row);changes=[]
  for old,new in pairs:
   value=cells.get(str(old['id']))
   if value not in (None,'') and cells.get(str(new['id']))!=value:
    changes.append({'columnId':new['id'],'value':value,'strict':False})
  if changes:updates.append({'id':row['id'],'cells':changes})
 for start in range(0,len(updates),100):api('PUT','/rows',json=updates[start:start+100])
 verified=api('GET','',params={'includeAll':'true'})
 verified_rows=complete(verified)
 for row in verified_rows:
  cells=values(row)
  for old,new in pairs:
   source=cells.get(str(old['id']))
   if source not in (None,'') and cells.get(str(new['id']))!=source:
    raise ValueError('Copy verification failed; legacy columns retained')
 # Back up the verified source again, including changes made during copying.
 backup({'columns':[c for pair in pairs for c in pair], 'rows':[{'id':r['id'],'cells':[c for c in r.get('cells',[]) if str(c['columnId']) in {str(col['id']) for pair in pairs for col in pair}]} for r in verified_rows]})
 on_verified(len(updates))
 removed=0
 for old,new in pairs:
  api('DELETE','/columns/'+str(old['id']));removed+=1
 return {'moved':len(updates),'removed':removed}
