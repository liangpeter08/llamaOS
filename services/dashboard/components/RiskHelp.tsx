"use client";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

export function RiskHelp({label,details}: {label:string; details:{description:string;example:string;interaction:string}}) {
  const [open,setOpen]=useState(false);
  const [pos,setPos]=useState({left:0,top:0,width:440});
  const anchor=useRef<HTMLButtonElement>(null);
  const panel=useRef<HTMLDivElement>(null);
  const timer=useRef<ReturnType<typeof setTimeout> | null>(null);
  const id=useId();
  function show() {
    if(timer.current) clearTimeout(timer.current);
    const r=anchor.current?.getBoundingClientRect();
    if(!r) return;
    const width=Math.min(440,window.innerWidth-24);
    setPos({left:Math.max(12,Math.min(r.left,window.innerWidth-width-12)),top:r.bottom+8,width});
    setOpen(true);
  }
  function hide() {timer.current=setTimeout(()=>setOpen(false),150);}
  useEffect(()=>{
    if(!open) return;
    function escape(e:KeyboardEvent) {if(e.key==="Escape") setOpen(false);}
    function outside(e:MouseEvent) {if(!anchor.current?.contains(e.target as Node) && !panel.current?.contains(e.target as Node)) setOpen(false);}
    function scroll(e:Event) {if(!panel.current?.contains(e.target as Node)) setOpen(false);}
    function resize() {setOpen(false);}
    document.addEventListener("keydown",escape);
    document.addEventListener("mousedown",outside);
    document.addEventListener("scroll",scroll,true);
    window.addEventListener("resize",resize);
    return ()=>{document.removeEventListener("keydown",escape);document.removeEventListener("mousedown",outside);document.removeEventListener("scroll",scroll,true);window.removeEventListener("resize",resize);};
  },[open]);
  useEffect(()=>()=>{if(timer.current) clearTimeout(timer.current);},[]);
  return <>
    <button ref={anchor} type="button" className="risk-help-trigger" onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide} onClick={show} aria-describedby={open ? id : undefined} aria-label={`About ${label}`}>
      {label} <span aria-hidden="true" className="risk-help-icon">ⓘ</span>
    </button>
    {open && createPortal(<div ref={panel} role="tooltip" id={id} className="risk-help-panel" onMouseEnter={()=>{if(timer.current) clearTimeout(timer.current);}} onMouseLeave={hide} style={{left:pos.left,top:Math.min(pos.top,Math.max(12,window.innerHeight-370)),width:pos.width,maxHeight:"min(350px, calc(100vh - 24px))"}}>
      <strong>{label}</strong>
      <p>{details.description}</p>
      <b>Example</b><p>{details.example}</p>
      <b>Related limits &amp; behavior</b><p>{details.interaction}</p>
      <small className="muted">Examples use illustrative values. Hover here to keep this open; press Escape to close.</small>
    </div>,document.body)}
  </>;
}
