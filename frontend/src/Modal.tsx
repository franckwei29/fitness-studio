import { useEffect, useRef, type ReactNode } from 'react';
import { SlidersHorizontal, X } from 'lucide-react';
export function Modal({title,onClose,children,icon}:{title:string;onClose:()=>void;children:ReactNode;icon?:ReactNode}){
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const before=document.activeElement as HTMLElement|null;ref.current?.showModal();return()=>{before?.focus();};},[]);
 return <dialog ref={ref} className="studio-modal" onCancel={e=>{e.preventDefault();onClose();}} aria-label={title}><header><span className="modal-symbol">{icon||<SlidersHorizontal size={22}/>}</span><h2>{title}</h2><button type="button" aria-label="Close dialog" className="text-button" onClick={onClose}><X/></button></header>{children}</dialog>;
}
