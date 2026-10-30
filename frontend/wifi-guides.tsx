import {Plus} from 'lucide-react';
const guides=[
 {title:'How do I reconnect my SunPower PVS6 to Wi-Fi?',terms:'sunstrong monitoring internet network router',steps:[
 'Open SunStrong Connect. Choose Profile → Wi-Fi Settings, or tap Reconnect System on the home screen.',
 'Go near your PVS6 and tap Found It. Stay within about six feet, then select Detect Device. Allow location and Bluetooth access if the app requests them.',
 'Select the detected device, tap Connect, then Connect Wi-Fi.',
 'Choose your home network, enter its password in the app, and tap Connect. Refresh the network list if needed.',
 'Allow up to 15 minutes for reconnection. Check the app again for updated monitoring.'
 ],note:'Use SunStrong Connect for this PVS6 guide. If the app cannot find your device, send our team the model and the error shown.',url:'https://us.sunpower.com/support/wifi-setup-solar-monitoring',source:'SunPower’s PVS6 guide'},
 {title:'How do I reconnect my Tesla Gateway to Wi-Fi?',terms:'powerwall internet network router',steps:[
 'Open the Tesla app and select your home energy site. Go to Settings → Configure Wi-Fi.',
 'If the system is offline, stand beside your Gateway throughout setup. Follow the app’s instructions for your model: Gateway 1 uses manual details; Gateway 2 uses its QR code behind the glass door.',
 'Accept the app’s request to join the local TEG network when prompted.',
 'Choose your home Wi-Fi network, enter its password in the Tesla app, and tap Connect. Allow up to five minutes.',
 'If the system already has cellular or Ethernet internet, the app can update Wi-Fi directly. For first-time setup, choose Network Setup.'
 ],note:'Gateway 1 requires 2.4 GHz; Gateway 2 supports 2.4 and 5 GHz. Powerwall 3 has a different app-guided Bluetooth procedure—use Tesla’s model-specific guide below.',url:'https://www.tesla.com/support/energy/powerwall/mobile-app/connecting-powerwall-wi-fi',source:'Tesla’s Wi-Fi guide'},
 {title:'How do I reconnect my Enphase combiner / IQ Gateway to Wi-Fi?',terms:'envoy enlighten internet network router',steps:[
 'Update and open the Enphase homeowner app. Select Connect Gateway to the Internet when the offline prompt appears.',
 'Tap Connect and follow the app’s instructions to turn on the IQ Gateway’s AP (access point) mode.',
 'Follow the phone prompts to connect to the Gateway’s local Wi-Fi network.',
 'Choose your home Wi-Fi network and enter its password in the app. Complete the remaining on-screen steps.',
 'Check for the app’s connection confirmation and allow monitoring data to refresh.'
 ],note:'On compatible IQ Combiners, the IQ Gateway handles monitoring. Button locations and screens vary by model; use the official guide and your equipment instructions.',url:'https://enphase.com/sites/default/files/2025-08/Enphase-solar-troubleshooting-guide-ING-00061-2.0-EN-INT-2025-08-14.pdf',source:'Enphase’s troubleshooting guide (page 11)'}
];
export function WifiGuides({search}:{search:string}){return <>{guides.filter(g=>(g.title+' '+g.terms+' '+g.steps.join(' ')).toLowerCase().includes(search.toLowerCase())).map(g=><details className="wifi-guide" key={g.title}><summary>{g.title}<Plus size={18} aria-hidden="true"/></summary><p>Have your home Wi-Fi name and password ready. Enter the password only in the manufacturer’s app; do not include it in a service ticket.</p><ol>{g.steps.map(s=><li key={s}>{s}</li>)}</ol><p>{g.note}</p><p><a href={g.url} target="_blank" rel="noreferrer">Open {g.source} ↗</a></p><p className="muted">Do not remove electrical covers or touch wiring. If setup fails or your screens differ, submit a Monitoring ticket with your equipment model and a screenshot of the error.</p></details>)}</>}
