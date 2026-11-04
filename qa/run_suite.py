"""Windows/Linux test orchestrator for Quality Dimensions v0.8.

Delivers one JSON, Markdown, JUnit XML and per-stage log; never treats SKIP
or unavailable optional dependencies as a successful engine integration.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
PACKAGES=['quaxdim','pytest','pandas','numpy','PyYAML','jsonschema',
          'duckdb','polars','pyspark','pyarrow','psutil','openpyxl','regex']


def installed_version(p):
    try:return importlib.metadata.version(p)
    except importlib.metadata.PackageNotFoundError:return None


def get_machine():
    try:
        import psutil
        memory_gb=round(psutil.virtual_memory().total/(1024**3),2)
        disk_free_gb=round(shutil.disk_usage(ROOT).free/(1024**3),2)
    except Exception:
        memory_gb=None;disk_free_gb=None
    java=None
    try:
        command=subprocess.run(['java','-version'],text=True,capture_output=True,timeout=10,
                               encoding='utf-8',errors='replace')
        java=(command.stderr or command.stdout).splitlines()[0] if command.returncode==0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):pass
    return {'platform':platform.platform(),'python':sys.version.split()[0],
            'executable':sys.executable,'architecture':platform.machine(),
            'processor_count':os.cpu_count(),'memory_gb':memory_gb,
            'free_disk_gb':disk_free_gb,'java':java,
            'dependency_versions':{x:installed_version(x) for x in PACKAGES}}


def summarize_xml(xmlpath):
    if not xmlpath.exists():return None
    root=ET.parse(xmlpath).getroot()
    cases=list(root.iter('testcase'))
    return {'tests':len(cases),'passed':sum(not any(c.find(x) is not None for x in ('failure','error','skipped')) for c in cases),
            'failed':sum(c.find('failure') is not None for c in cases),
            'errors':sum(c.find('error') is not None for c in cases),
            'skipped':sum(c.find('skipped') is not None for c in cases)}


def run_step(name, command, output, *, timeout=1200, skip_reason=None, warning_only=False):
    info={'name':name,'status':'NOT_TESTED','returncode':None,'seconds':None,'counts':None,
          'reason':None,'command':command,'log':None,'junit':None}
    if skip_reason:
        info['reason']=skip_reason
        print(f'[NOT_TESTED] {name}: {skip_reason}',flush=True)
        return info
    started=time.monotonic()
    logfile=output/(name+'.log')
    xmlfile=output/(name+'.xml') if '--junitxml' in command else None
    info['log']=logfile.name
    if xmlfile:info['junit']=xmlfile.name
    print(f'[RUN] {name}',flush=True)
    env=os.environ.copy()
    env['PYTHONPATH']=str(ROOT/'src') + os.pathsep + env.get('PYTHONPATH','')
    env['PYTHONIOENCODING']='utf-8'
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    try:
        p=subprocess.run(command,env=env,cwd=ROOT,text=True,capture_output=True,
                         encoding='utf-8',errors='replace',timeout=timeout)
        data=(p.stdout or '')+'\n--- STDERR ---\n'+(p.stderr or '')
        logfile.write_text(data,encoding='utf-8')
        info['returncode']=p.returncode
        info['seconds']=round(time.monotonic()-started,3)
        if xmlfile:info['counts']=summarize_xml(xmlfile)
        info['status']='PASS' if p.returncode==0 else 'FAIL'
        if info['counts']:
            c=info['counts']
            if c['tests']==0: info['status']='FAIL';info['reason']='No tests collected'
            elif c['skipped']>0 and c['passed']==0 and p.returncode==0:
                info['status']='NOT_TESTED';info['reason']='All tests skipped'
        if info['status']=='FAIL' and warning_only:
            info['status']='WARN'
            info['reason']='Entorno: revisar conflictos de dependencias; esto no invalida por sí solo el motor.'
        if info['status'] in {'FAIL','WARN'}:
            info['reason']=info['reason'] or 'Nonzero exit code: inspect log'
            print(f"[{info['status']}] {name}, exit={p.returncode}, log={logfile.name}",flush=True)
            # display a short tail, leaving full log on disk
            print('\n'.join(data.splitlines()[-18:]),flush=True)
        else:
            counts=info['counts'] or {}
            print(f"[{info['status']}] {name} ({info['seconds']} s) "
                  f"passed={counts.get('passed','-')} skipped={counts.get('skipped','-')}",flush=True)
    except subprocess.TimeoutExpired as ex:
        info['seconds']=round(time.monotonic()-started,3)
        info['status']='FAIL';info['reason']=f'Timeout after {timeout} seconds'
        logfile.write_text(f'TIMEOUT: {ex}\n',encoding='utf-8')
        print(f'[FAIL] {name}: timeout',flush=True)
    except Exception as ex:
        info['seconds']=round(time.monotonic()-started,3)
        info['status']='FAIL';info['reason']=f'Runner error: {ex}'
        logfile.write_text(traceback.format_exc(),encoding='utf-8')
        print(f'[FAIL] {name}: {ex}',flush=True)
    return info


def pytest_command(name, *args, output):
    return [sys.executable,'-m','pytest','-q','--tb=short','--disable-warnings',
            '--junitxml',str(output/(name+'.xml')), *args]


def write_reports(payload, output):
    (output/'resultados.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    lines=['# Informe automatizado — Quality Dimensions', '',
           f"**Perfil:** {payload['profile']} | **Fecha UTC:** {payload['utc']} | **Resultado:** {payload['overall']}",
           '', '## Entorno',
           f"- Python: `{payload['machine']['python']}` | Sistema: `{payload['machine']['platform']}`",
           f"- Java: `{payload['machine']['java'] or 'NO DETECTADO'}`",
           f"- RAM: {payload['machine']['memory_gb']} GB | Disco libre: {payload['machine']['free_disk_gb']} GB",
           '', '## Etapas', '',
           '| Etapa | Estado | Pasaron | Fallaron | Omitidos | Segundos |',
           '|---|---|---:|---:|---:|---:|']
    for s in payload['stages']:
        c=s['counts'] or {}
        lines.append(f"| {s['name']} | **{s['status']}** | {c.get('passed','—')} | "
                     f"{c.get('failed','—')} | {c.get('skipped','—')} | {s['seconds'] or '—'} |")
    not_tested=[s for s in payload['stages'] if s['status'] in {'NOT_TESTED','WARN'}]
    failing=[s for s in payload['stages'] if s['status']=='FAIL']
    lines.extend(['','## Pendientes / advertencias'])
    for s in not_tested:lines.append(f"- **{s['name']}**: {s['reason'] or 'No ejecutada'}")
    if not not_tested:lines.append('- Sin etapas omitidas.')
    lines.extend(['','## Fallos'])
    for s in failing:lines.append(f"- **{s['name']}**: {s['reason']} (log: `{s['log']}`).")
    if not failing:lines.append('- No se registraron fallos de ejecución.')
    lines.extend(['','## Alcance y límites',
       '- `PASS` únicamente certifica las pruebas incluidas; no demuestra calidad universal.',
       '- `NOT_TESTED` indica un motor ausente o una fase omitida; nunca cuenta como aprobación.',
       '- Estas pruebas usan datos sintéticos salvo que se proporcione `--input` explícitamente.',
       '- Spark Connect y Structured Streaming no están verificados; DuckDB/Polars/Spark son experimentales hasta superar sus integraciones nativas.',
       '- La equivalencia se comprueba solo en semánticas comparables. NULL/blank/NaN pueden diferir entre motores.',
       '- Los informes de descubrimiento no incluyen muestras de filas; los logs pueden contener errores técnicos.',
       '', '## Cómo reportar problemas',
       'Compartir `INFORME_PRUEBAS.md`, `resultados.json`, y los `.log` de las etapas con fallo.',
       'Revisar logs antes de compartir si se utilizó un archivo privado con `--input`.',
       '', '## Entorno y dependencias', '', '```json',
       json.dumps(payload['machine']['dependency_versions'],ensure_ascii=False,indent=2), '```'])
    (output/'INFORME_PRUEBAS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    ap=argparse.ArgumentParser(description='Testkit de calidad v0.8')
    ap.add_argument('--mode',choices=['rapido','completo','estres'],default='completo')
    ap.add_argument('--rows',type=int,default=None)
    ap.add_argument('--input',type=Path,default=None,help='Archivo real opcional, solo lectura')
    ap.add_argument('--engine',choices=['auto','csv_stream','duckdb','polars'],default='auto')
    ap.add_argument('--strict-backends',action='store_true',help='Fallar si faltan DuckDB, Polars o Spark')
    ap.add_argument('--output',type=Path,default=None)
    args=ap.parse_args()
    if args.rows is not None and not (1 <= args.rows <= 100_000_000):
        ap.error('--rows fuera de rango permitido 1..100000000')
    if args.input is not None and not args.input.exists():ap.error('--input no existe')
    if args.strict_backends and args.mode=='rapido':
        ap.error('--strict-backends requiere --mode completo o estres')
    date=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    output=(args.output or ROOT/'reportes'/date).resolve()
    output.mkdir(parents=True,exist_ok=True)
    machine=get_machine()
    print(f"Quality Dimensions testkit | {args.mode} | {machine['python']} | salida: {output}",flush=True)
    stages=[]
    def stage(name,cmd,**kw):
        result=run_step(name,cmd,output,**kw)
        # Do not embed the local private-input path in shareable reports.
        if args.input is not None:
            result['command']=['<RUTA_ARCHIVO_REAL>' if x==str(args.input) else x
                               for x in result['command']]
        stages.append(result)
        payload={'profile':args.mode,'utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                 'machine':machine,'stages':stages,'strict_backends':args.strict_backends,
                 'overall':('FAIL' if any(s['status']=='FAIL' for s in stages) else 'PARTIAL'
                            if any(s['status'] in {'NOT_TESTED','WARN'} for s in stages) else 'PASS')}
        write_reports(payload,output)
    stage('codigo_compila',[sys.executable,'-m','compileall','-q',str(ROOT/'src'),str(ROOT/'qa'),str(ROOT/'tests'),str(ROOT/'tests_validation')])
    stage('dependencias_consistentes',[sys.executable,'-m','pip','check'],timeout=90,warning_only=True)
    stage('regresion_v07',pytest_command('regresion_v07','tests','--ignore=tests/test_spark_backend.py',output=output))
    stage('csv_y_cli',pytest_command('csv_y_cli','tests_validation/test_csv_acceptance.py',output=output))
    stage('contratos_y_seguridad',pytest_command('contratos_y_seguridad','tests_validation/test_contract_and_safety.py',output=output))
    stage('compatibilidad_pandas3',pytest_command('compatibilidad_pandas3','tests_validation/test_pandas_readonly_masks.py',output=output))
    stage('v08_ejecucion_nativa_csv',pytest_command('v08_ejecucion_nativa_csv',
           'tests_validation/test_v08_optimized.py','tests_validation/test_v08_sql_compiler.py',output=output))

    if args.mode != 'rapido':
        optional=[('duckdb','duckdb'),('polars','polars'),('spark','pyspark')]
        for name,mod in optional:
            found=importlib.util.find_spec(mod) is not None
            if name=='spark':
                java_missing = not bool(machine['java'])
                spark_reason = ('PySpark no instalado' if not found else
                                'Java no detectado; instalar JDK 17+ y verificar PATH/JAVA_HOME' if java_missing else None)
                stage('spark_regresion',pytest_command('spark_regresion','tests/test_spark_backend.py',output=output),
                      timeout=1200,skip_reason=spark_reason)
                stage('spark_discovery',pytest_command('spark_discovery','tests_validation/test_optional_engines.py','-k','spark',output=output),
                      timeout=1200,skip_reason=spark_reason)
            else:
                stage(name+'_native',pytest_command(name+'_native','tests_validation/test_optional_engines.py','-k',name,output=output),
                      timeout=600,skip_reason=None if found else f'{mod} no instalado')
        for backend,module in [('duckdb','duckdb'),('polars','polars'),('spark','pyspark')]:
            exists=importlib.util.find_spec(module) is not None
            blocker = (f'{module} no instalado' if not exists else
                       'Java no detectado; pendiente de instalar JDK' if backend=='spark' and not machine['java'] else None)
            stage(f'v08_paridad_{backend}',pytest_command(f'v08_paridad_{backend}',
                  'tests_validation/test_v08_native_optional.py','-k',backend,output=output),
                  timeout=1200,skip_reason=blocker)
        has_duck=importlib.util.find_spec('duckdb') is not None
        has_polars=importlib.util.find_spec('polars') is not None
        stage('paridad_duckdb_polars',pytest_command('paridad_duckdb_polars','tests_validation/test_backend_parity.py',output=output),
              skip_reason=None if has_duck and has_polars else 'Requiere DuckDB y Polars')
        stage('construccion_wheel',[sys.executable,'-m','pip','wheel','--no-deps','--no-build-isolation',
                                   '--wheel-dir',str(output/'wheels'),str(ROOT)],timeout=240)
    nrows=args.rows if args.rows else {'rapido':20000,'completo':200000,'estres':2000000}[args.mode]
    stage('rendimiento_csv',[sys.executable,str(ROOT/'qa'/'benchmark_workload.py'),
         '--rows',str(nrows),'--output',str(output/'benchmark.json')],timeout=1800)
    # Identical v0.8 rules and fixture across all engines. CSV stream is always available.
    stage('v08_rendimiento_csv',[sys.executable,str(ROOT/'qa'/'benchmark_v08.py'),
          '--engine','csv_stream','--rows',str(nrows),
          '--output',str(output/'v08_benchmark_csv.json')],timeout=1800)
    if args.mode != 'rapido':
        for backend,module in [('duckdb','duckdb'),('polars','polars'),('spark','pyspark')]:
            installed=importlib.util.find_spec(module) is not None
            blocker = (f'{module} no instalado' if not installed else
                       'Java no detectado' if backend=='spark' and not machine['java'] else None)
            stage(f'v08_rendimiento_{backend}',
                  [sys.executable,str(ROOT/'qa'/'benchmark_v08.py'),
                   '--engine',backend,'--rows',str(nrows),
                   '--output',str(output/f'v08_benchmark_{backend}.json')],
                  timeout=1800,skip_reason=blocker)

    if args.mode != 'rapido':
        # Native engine scaling: no transfer of raw records to pandas.
        for backend,module in [('duckdb','duckdb'),('polars','polars'),('spark','pyspark')]:
            exists=importlib.util.find_spec(module) is not None
            blocker = (f'{module} no instalado' if not exists else
                       'Java no detectado; pendiente de instalar JDK' if backend == 'spark' and not machine['java'] else None)
            stage(f'rendimiento_{backend}',
                  [sys.executable,str(ROOT/'qa'/'benchmark_engines.py'),
                   '--engine',backend,'--rows',str(nrows),
                   '--output',str(output/f'benchmark_{backend}.json')],timeout=1800,
                  skip_reason=blocker)
    if args.input is not None:
        stage('archivo_real_solo_lectura',[sys.executable,str(ROOT/'qa'/'inspect_input.py'),
              '--input',str(args.input),'--output',str(output/'descubrimiento_archivo_real.json'),
              '--engine',args.engine],timeout=1800)
    failure=any(s['status']=='FAIL' for s in stages)
    untested=any(s['status'] in {'NOT_TESTED','WARN'} for s in stages)
    overall='FAIL' if failure or (args.strict_backends and untested) else ('PARTIAL' if untested else 'PASS')
    payload={'profile':args.mode,'utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
             'machine':machine,'stages':stages,'strict_backends':args.strict_backends,'overall':overall}
    write_reports(payload,output)
    archive=output.with_suffix('.zip')
    with ZipFile(archive,'w',ZIP_DEFLATED,compresslevel=6) as z:
        for f in sorted(output.iterdir()):
            # Exclude user input profiles and wheels; prevent accidental data disclosure.
            if f.is_file() and f.name!='descubrimiento_archivo_real.json':
                z.write(f,arcname=f.name)
    print(f'\nRESULTADO FINAL: {overall}',flush=True)
    print(f'ZIP para revision (sin datos de archivo real): {archive}',flush=True)
    print(f'Informe: {output/"INFORME_PRUEBAS.md"}',flush=True)
    print(f'JSON: {output/"resultados.json"}',flush=True)
    return 1 if overall=='FAIL' else 0


if __name__=='__main__':sys.exit(main())
