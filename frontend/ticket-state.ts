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
