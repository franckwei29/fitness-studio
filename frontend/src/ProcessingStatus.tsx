import {Clock3, LoaderCircle} from 'lucide-react';

/** A spinner means active work, never an unresolved/manual-review wait. */
export function ProcessingStatus({status,busy=false}:{status:string;busy?:boolean}) {
 const running=busy||status==='Running';
 const queued=!busy&&status==='Queued';
 const paused=!busy&&status==='Waiting';
 if(!running&&!queued&&!paused)return null;
 return <p role="status" className="processing-status" aria-live="polite">
  {running?<LoaderCircle className="spin" size={18} aria-hidden="true"/>:<Clock3 size={18} aria-hidden="true"/>}
  <span>{busy?'Saving your action…':running?'Working on your request. Updates will appear here automatically.':queued?'Your request is queued. It will start when a worker is available.':'Preparation is paused. Review the explanation below; refreshing this page will not restart the request.'}</span>
 </p>;
}
