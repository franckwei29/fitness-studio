export type FieldPolicy = Record<string,string[]>;
export const fieldLabel=(name:string)=>name.replaceAll('_',' ').replace(/^./,letter=>letter.toUpperCase());
export const roleLabel=(name:string)=>name==='TechnicalManager'?'Technical Manager':name;
export const accessLabel=(fields:string[]|undefined)=>fields===undefined?'Standard role access':fields.length?fields.map(fieldLabel).join(', '):'No profile fields';

export function FieldPermissions({policy,value,onChange}:{policy:FieldPolicy;value:FieldPolicy;onChange:(next:FieldPolicy)=>void}){
  return <section><h3>Profile field access</h3>
    <p className="muted">Limit which confirmed profile fields each role can read. Changes require approval and execution. Removing fields needed for a service may stop that service until its access is restored. Fixed role boundaries cannot be expanded here.</p>
    {Object.entries(policy).map(([role,allowed])=>{
      const standard=value[role]===undefined;
      const selected=value[role]??allowed;
      return <fieldset key={role}><legend>{roleLabel(role)}</legend>
        {!allowed.length?<p className="muted">This role has no access to personal profile fields.</p>:<>
          <label className="confirmation"><input type="checkbox" checked={standard} onChange={event=>{
            const next={...value};if(event.target.checked)delete next[role];else next[role]=[...allowed];onChange(next);
          }}/>Use standard {roleLabel(role)} access</label>
          {allowed.map(field=><label className="confirmation" key={field}><input type="checkbox" aria-label={`${roleLabel(role)}: ${fieldLabel(field)}`} checked={selected.includes(field)} disabled={standard} onChange={event=>onChange({...value,[role]:event.target.checked?[...selected,field]:selected.filter(item=>item!==field)})}/>{fieldLabel(field)}</label>)}
        </>}
      </fieldset>;
    })}
  </section>;
}
