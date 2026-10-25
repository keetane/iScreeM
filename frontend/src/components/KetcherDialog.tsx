import {useEffect,useMemo,useState} from 'react'
import {Check,FlaskConical,X} from 'lucide-react'
import {Editor} from 'ketcher-react'
import type {Ketcher} from 'ketcher-core'
import {StandaloneStructServiceProvider} from 'ketcher-standalone'
import 'ketcher-react/dist/index.css'

type Props={
  open:boolean
  onClose:()=>void
  onImport:(smiles:string,name:string)=>void
}

export function KetcherDialog({open,onClose,onImport}:Props){
  const provider=useMemo(()=>new StandaloneStructServiceProvider(),[])
  const [ketcher,setKetcher]=useState<Ketcher>()
  const [name,setName]=useState('Ketcher ligand')
  const [error,setError]=useState('')
  const [exporting,setExporting]=useState(false)

  useEffect(()=>{
    if(!open)return
    const close=(event:KeyboardEvent)=>{if(event.key==='Escape')onClose()}
    window.addEventListener('keydown',close)
    return()=>window.removeEventListener('keydown',close)
  },[open,onClose])

  if(!open)return null

  const importStructure=async()=>{
    if(!ketcher)return
    setExporting(true);setError('')
    try{
      const smiles=(await ketcher.getSmiles()).trim()
      if(!smiles)throw new Error('構造が描画されていません。')
      onImport(smiles,name.trim()||'Ketcher ligand')
      onClose()
    }catch(reason){
      setError(reason instanceof Error?reason.message:String(reason))
    }finally{
      setExporting(false)
    }
  }

  return <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 p-5" role="dialog" aria-modal="true" aria-label="Ketcher structure editor">
    <div className="flex h-[min(860px,94vh)] w-[min(1440px,96vw)] flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-2xl">
      <div className="flex items-center gap-3 border-b border-line px-5 py-3">
        <FlaskConical className="h-5 w-5 text-cyan"/>
        <div><div className="text-sm font-semibold">Ketcher Structure Editor</div><div className="text-xs text-slate-500">構造を描画し、SMILESとしてLigand Libraryへ追加します</div></div>
        <button className="icon-btn ml-auto" aria-label="Close Ketcher" onClick={onClose}><X/></button>
      </div>
      <div className="min-h-0 flex-1 bg-white">
        <Editor staticResourcesUrl={import.meta.env.BASE_URL} structServiceProvider={provider} onInit={setKetcher} errorHandler={setError} disableMacromoleculesEditor/>
      </div>
      <div className="flex items-center gap-3 border-t border-line bg-panel px-5 py-3">
        <label className="flex min-w-0 flex-1 items-center gap-3"><span className="text-xs font-semibold uppercase tracking-wider text-slate-500">Ligand name</span><input className="input max-w-sm" value={name} onChange={event=>setName(event.target.value)} placeholder="Ligand name"/></label>
        {error&&<div className="max-w-md text-xs text-red-300">{error}</div>}
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={!ketcher||exporting} onClick={importStructure}><Check/>{exporting?'Converting…':'Import structure'}</button>
      </div>
    </div>
  </div>
}
