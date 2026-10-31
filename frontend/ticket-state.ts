// Merge by private request identity: repeat customers deliberately share a reference.
export function mergeSubmittedTicket(tickets:any[], incoming:any) {
 const previous=tickets.find(ticket=>ticket.id===incoming.id);
 const merged=previous?.deliveryStatus==='delivered'&&incoming.deliveryStatus!=='delivered'
  ? {...incoming,...previous} : {...previous,...incoming};
 return [merged,...tickets.filter(ticket=>ticket.id!==incoming.id)];
}

export function receiptReady(ticket:any) {
 return ticket?.deliveryStatus==='delivered' && typeof ticket.reference==='string' && ticket.reference.trim().length>0;
}

// Presentation only: preserve the source status for workflow and warranty rules.
const customerStatuses:Record<string,string> = {
 '000':'Ticket Received', '010':'Reviewing Ticket', '020':'Contacted',
 '023':'Pending', '024':'Requested Replacement', '025':'Scheduled',
 '030':'Completed', '031':'Completed by Phone', '032':'Completed by Text',
 '040':'Follow up', '050':'Under Review by Service Team', '051':'Resolution in Progress'
};
export function customerStatus(status:string) {
 const value=(status||'').trim();
 const code=value.match(/^(\d{3})(?=$|[\s–—-])/ )?.[1];
 return (code&&customerStatuses[code])||value||'Status unavailable';
}
