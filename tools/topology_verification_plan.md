# Plan de verificación — un cambio hecho en Blender no llega al subtool de ZBrush

**Síntoma:** ZBrush → Blender → (añadir/quitar caras) → Blender → ZBrush, y el
subtool de ZBrush conserva la malla vieja. Con cambios que **no** alteran la
topología (mover vértices) parece funcionar.

Todo lo marcado como *medido* se ejecutó en esta máquina: Blender 5.2.2 LTS +
ZBrush 2026.2.1 (build 53205), con las sondas de este directorio.

---

## 1. Resultados medidos

### 1.1 Lado Blender: correcto

`tools/verify_topology_export.py` ejecuta el operador real `GoB_OT_export`
cuatro veces con el mismo nombre de objeto y lee las cabeceras del `.GoZ`:
8v/6f → 8v/6f (vértices movidos) → **26v/24f** (subdividido) → **8v/4f** (caras
borradas). `VERDICT ALL_PASS`, 24/24. **Blender no pierde el cambio.**

### 1.2 Lado ZBrush: el recuento de puntos es irrelevante

`tools/zbrush_topology_probe.py`, dentro de una sola sesión:

| Escenario | Resultado |
|---|---|
| Import en un subtool normal | 8v / 6f |
| Ese subtool tras 2 × `Tool:Geometry:Divide` | 98v / 96f, SDiv 3 |
| Re-import **mismo** point count, vértices movidos | aplicado |
| Re-import **point count distinto** (26v/24f) | **aplicado**, SDiv vuelve a 1 |
| Re-import 8v/4f con 2 subtools y el subtool 0 activo | **aplicado al subtool 0** |

La hipótesis "ZBrush no actualiza si cambia el número de puntos" **queda
refutada**.

### 1.3 El mecanismo real: ZBrush empareja por **nombre de fichero**

El A/B (`.probe/ab/`, script antiguo y arreglado en la misma sesión) mostró la
regla que decide todo:

| Fichero importado | Subtools existentes | Resultado |
|---|---|---|
| `GoBTopoProbe.GoZ` | `GoBTopoProbe.` (el activo) | **reemplaza ese subtool** en el sitio |
| `GoBTopoProbe.GoZ` | `base.`, `base1.` | **añade un subtool nuevo** llamado `GoBTopoProbe` |
| `base.GoZ` | `base.`, `base1.` (activo: `base1`) | el activo pasa a llamarse `base` |

Dos hechos, medidos y no deducidos:

1. **El subtool toma el nombre del fichero, no el de la cabecera del `.GoZ`.** El
   fichero `base.GoZ` contiene `GoZMesh_GoBTopoProbe` y el subtool acabó
   llamándose `base`. El nombre del objeto que escribe Blender sólo importa
   porque Blender bautiza el fichero con él (`gob_export.py:146`).
2. **Si ningún subtool se llama como el fichero, ZBrush añade uno nuevo** y los
   existentes se quedan con la malla vieja. Ese es el síntoma: el cambio sí
   llega, pero a un subtool nuevo, no al que se estaba esculpiendo.

   Matiz honesto: cuando el nombre **sí** existe, estas medidas no distinguen si
   ZBrush actualiza ese subtool o el que esté activo (en los casos probados
   coincidían). El caso que explica el síntoma —nombre inexistente— es
   inequívoco.

Por eso el recuento de puntos nunca fue el discriminador, y por eso la lógica
`FindSubtool` / `UpdateSubTool` / `AddSubTool` del ZScript **no decide nada**: es
ZBrush quien resuelve el import.

### 1.4 Defecto encontrado de paso (arreglado)

`ZScripts/GoB_Import.txt` recortaba dos caracteres al título del subtool
(`'GoBTopoProbe.'` → `'GoBTopoProb'`) y `FindSubtool` exige longitud exacta, así
que la ruta de actualización **nunca se ejecutaba** en ZBrush 2026.2.1. Con el
mecanismo de 1.3 el resultado final era el mismo, pero la ruta estaba muerta y
con ella el forzado `Tool:Geometry:SDiv = 1`. Arreglado en
`ZScripts/GoB_Import.txt`: se conserva el título tal cual y `FindSubtool` acepta
las tres formas (`Nombre`, `Nombre.`, `Nombre .`).

---

## 2. Cómo se ejecuta cada sonda

Lado Blender (sin GUI):

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --background `
  --python "D:\projectos\gob_jeisson\GoB\tools\verify_topology_export.py"
```

Lado ZBrush: **`-script` no sirve en esta máquina.** ZBrush abre su Home Page
(proceso aparte) y el script nunca corre; cerrarla cierra ZBrush. La vía que
funciona es el hook de plugins documentado por Maxon:

```powershell
$env:ZBRUSH_PLUGIN_PATH = "<dir con una sola sonda .py>"
& "C:\Program Files\Maxon ZBrush 2026\ZBrush.exe"
```

ZBrush ejecuta todos los `*.py` de ese directorio al arrancar (CPython 3.11.9
embebido, `zbrush.commands`). Deja **una sola** sonda en el directorio. Los
informes van a `.probe/` y a `~/gob_*.txt`. Si ZBrush viene de un cierre forzado
muestra un `Note` que bloquea el arranque: hay que pulsar "Aceptar".

El A/B de 1.3 se reproduce con `.probe/ab/prepare_handshake.py` (escribe el
handshake en `C:\Users\Public\Pixologic`, con copia de seguridad de lo que había)
y `.probe/ab/plugin/gob_ab_test.py`. **Restaurar** el handshake después.

---

## 3. Checklist para el caso real (lo que hay que mirar)

| # | Comprobación | Por qué |
|---|---|---|
| 3.1 | Tras exportar, ¿aparece un **subtool nuevo** o se actualiza el existente? | si aparece uno nuevo, el nombre no coincidía |
| 3.2 | ¿El nombre del objeto en Blender es **exactamente** el del subtool, sin `.001`? | el fichero y el subtool deben llamarse igual |
| 3.3 | ¿El nombre tiene espacios, acentos o puntos? | `escape_object_name` (`gob_export.py:815-842`) los convierte en `_`: `left arm` → `left_arm`, `finger2.001` → `finger2_001` |
| 3.4 | ¿Cuántos subtools tiene el tool antes y después? | ZBrush añade en vez de actualizar cuando no hay coincidencia |
| 3.5 | ¿ZBrush estaba ya abierto al exportar? | GoB lanza `Popen([ZBrush.exe, GoB_Import.txt])` |
| 3.6 | Preferencia `Modifiers` de GoB | con `IGNORE`, las caras que añade un modificador no salen en el `.GoZ` |

---

## 4. Estado de los cambios

Hecho:

* `ZScripts/GoB_Import.txt`: recorte del título eliminado y `FindSubtool` con las
  tres formas aceptadas (1.4).
* Sincronizado con el addon instalado en Blender 5.2
  (`extensions\user_default\gob\ZScripts\GoB_Import.txt`), con copia previa en
  `.probe/backup_pixologic/`.
* Apartado el `GoB_Import.zsc` viejo que ZBrush cargaba al arrancar desde
  `ZStartup\ZPlugs64\` (copia en `.probe/backup_pixologic/GoB_Import.zsc.disabled`).

Pendiente, y es el arreglo que ataca la causa medida en 1.3:

1. **No romper el nombre.** `escape_object_name` debería limitarse a los
   caracteres que ZBrush rechaza de verdad (`\ / : * ? " < > |`) y dejar puntos y
   espacios, que ZBrush acepta en nombres de subtool.
2. **Avisar cuando el nombre exportado no es el que vino de ZBrush.** GoB conoce
   el nombre del `.GoZ` original al importar; si al exportar no coincide, decirlo
   y ofrecer exportar con el nombre original, en vez de crear un subtool
   duplicado en silencio.
3. **Nombres duplicados en Blender** (`.001`): exportar con el nombre del
   original cuando el objeto venga de un GoZ con ese nombre.

Verificación de cada uno: `tools/verify_topology_export.py` (lado Blender) y el
A/B de `.probe/ab/` (lado ZBrush), que ya distingue "reemplaza" de "añade".

---

## 5. El caso "ZBrush ya está abierto"

Síntoma observado por el usuario, y reproducible en la evidencia de su propia
sesión: exportar con ZBrush **cerrado** funciona (ZBrush arranca y aparece el
modelo), pero en el ciclo ZBrush → Blender → ZBrush, con ZBrush abierto, el
subtool no se actualiza.

### 5.1 La causa

`gob_export.py:808` hace:

```python
Popen([zbrush_exec, launch_script])     # ZBrush.exe <ruta>\ZScripts\GoB_Import.txt
```

Es decir, **arranca un ZBrush nuevo** y le pasa el script. Nunca entrega el
fichero a la instancia que ya está abierta. Y en ZBrush 2026.2.1 el arranque se
queda en la Home Page (medido: `-script` no llega a ejecutarse), así que la
instancia nueva puede ni siquiera importar. La sesión abierta del usuario se
queda con la malla vieja: exactamente el síntoma.

Evidencia de su sesión del 2026-10-02 (snapshot de `tools/goz_handshake_snapshot.py`):

| Hora | Qué pasó |
|---|---|
| 21:02:31 | Blender escribe **todo** el handshake: `finger2.GoZ` (2897 B, 78v/73f, **sin** sección Subdivision → lo escribió Blender), `finger2.ztn`, `GoZ_ObjectList.txt`, `GoB_variables.zvr`, `GoZ_ProjectPath.txt`, `GoZApps\Blender\GoZ_Config.txt` |
| 21:02:35 | **un** ZBrush carga el script de GoB (escribe `GoB_Import_Settings.zvr` y compila `GoB_Import.zsc` en la carpeta del addon) |
| 21:02:47-21:02:59 | un ZBrush arranca (`uistates.json`, `settings.json`, `installpath.txt`) y reescribe `GoZBrush\GoZ_Config.txt` |

O sea: el fichero viajó, y lo consumió **otra** instancia.

### 5.2 Lo que Pixologic ofrece y GoB no usa

`C:\Users\Public\Pixologic\GoZBrush\GoZBrushFromApp.exe` (109 KB) es el
mecanismo oficial para que una aplicación entregue un modelo a un ZBrush
**en ejecución**. Sus cadenas revelan los ficheros que lee y el script que
dispara:

```
GoZ_Application.txt   GoZ_ProjectPath.txt   GoZ_ObjectPath.txt
GoZ_ObjectList.txt    GoZ_Config.txt
Scripts/GoZBrushFromAppScript.zsc
Scripts/GoZ_LoadTextureMaps.zsc
```

En GoB no aparece ni una referencia a él (`grep GoZBrushFromApp` → 0). GoB
inventa su propio marcador `<nombre>.ztn` y arranca ZBrush a mano.

Prueba hecha: con ZBrush abierto (pid 10512) y el handshake preparado,
`GoZBrushFromApp.exe` **no arrancó ningún proceso nuevo** (siguió habiendo un
solo ZBrush). Es la vía que habla con la instancia abierta.

### 5.3 Qué ficheros revisar (y en qué orden)

Comando único, antes y después del export:

```powershell
python tools/goz_handshake_snapshot.py --save
```

| # | Fichero | Qué mirar |
|---|---|---|
| 1 | `GoZBrush\GoZ_ObjectList.txt` | mtime y contenido; si no cambia, Blender no llegó a escribir el handshake |
| 2 | `GoZProjects\Default\<nombre>.GoZ` | mtime + recuentos de cabecera; sin sección `Subdivision` = escrito por Blender |
| 3 | `GoZProjects\Default\<nombre>.ztn` | marcador de GoB (el protocolo oficial usa `GoZBrush\GoZ_ObjectPath.txt`, que GoB **no** escribe) |
| 4 | `GoZProjects\Default\GoB_variables.zvr` | extensión, sufijos de texturas, versión y `gozProjectPath` que lee el ZScript |
| 5 | `GoZBrush\GoZ_Config.txt` | `IMPORT_AS_SUBTOOL = TRUE/FALSE`; ZBrush lo reescribe al arrancar |
| 6 | `GoZBrush\GoZ_Application.txt` y `GoZ_ProjectPath.txt` | app de destino y carpeta del proyecto |
| 7 | `GoZApps\Blender\GoZ_Config.txt` | `PATH = blender.exe`; es lo que hace funcionar ZBrush → Blender |
| 8 | Procesos `ZBrush.exe` | **si aparece uno nuevo** tras el export, la sesión abierta no fue servida |
| 9 | `%APPDATA%\Maxon\...\ZStartup\ZPlugs64\` | scripts de GoB que ZBrush carga al arrancar (`.zsc` es caché compilada: ZBrush la regenera al cargar un `.txt`) |
| 10 | `%APPDATA%\Blender Foundation\Blender\5.2\extensions\user_default\gob\ZScripts\` | el `GoB_Import.txt` que Blender le pasa a ZBrush, y su `.zsc`/`.zvr` recién escritos = señal de que el script se ejecutó |

**Regla de decisión:** fichero 1 sin cambios → fallo en Blender. Fichero 2 con
recuentos viejos → fallo en la exportación. Fichero 8 con un proceso nuevo →
fallo en la entrega (el caso del usuario). Subtool nuevo en ZBrush en vez de
actualizado → fallo de nombre (sección 1.3).

### 5.4 Arreglo propuesto

1. Si hay un `ZBrush.exe` en ejecución y existe `GoZBrushFromApp.exe`, entregar
   con el helper oficial: escribir `GoZBrush\GoZ_ObjectPath.txt` (y
   `GoZ_ObjectList.txt`/`GoZ_ProjectPath.txt` como ahora) y ejecutar
   `GoZBrushFromApp.exe` en lugar de `Popen([zbrush, GoB_Import.txt])`.
2. Si ZBrush no está en ejecución, mantener el arranque actual con el ZScript
   (funciona, es lo que el usuario ve funcionar).
3. Seguir escribiendo `<nombre>.ztn` por compatibilidad, y añadir
   `GoZ_ObjectPath.txt` para el camino oficial.
4. Nombrar el `.GoZ` con el nombre del subtool original (sección 4.1) para que
   ZBrush actualice en vez de añadir.

### 5.5 Estado: arreglo implementado y medido (falta la confirmación visual)

La invocación oficial, sacada de `GoZApps\Maya\GoZBrushFromMaya.mel` (líneas
527-595), es: escribir `GoZ_ObjectList.txt`, `GoZ_ProjectPath.txt`,
`GoZ_Application.txt` y **lanzar `GoZBrush/GoZBrushFromApp.exe` sin argumentos**.

Implementado:

| Fichero | Cambio |
|---|---|
| `paths.py` | `find_goz_from_app_helper()`, `zbrush_is_running()`, `goz_object_path_file()` |
| `gob_export.py` | escribe `GoZBrush\GoZ_ObjectPath.txt` (que GoB nunca escribía) y, **si hay un ZBrush abierto**, entrega con el helper en vez de lanzar `ZBrush.exe` |
| `ZScripts/GoB_Import.txt` | el arreglo del recorte del título (sección 1.4) |

Medido con `tools/repro_export_to_zbrush.py` (Blender headless, GoZ root real,
`export_run_zbrush = True`, ZBrush abierto):

```
INFO | helper        : C:\Users\Public\Pixologic\GoZBrush\GoZBrushFromApp.exe
INFO | zbrush running: True
GoB: handing the export to the running ZBrush: ...\GoZBrushFromApp.exe
INFO | GoZ_ObjectList.txt  63 bytes  | .../Default/GoBHandoffProbe
INFO | GoZ_ObjectPath.txt  61 bytes  | .../Default/GoBHandoffProbe   <-- nuevo
INFO | GoZ_Application.txt  7 bytes  | Blender
```

Y **no apareció ninguna instancia nueva de ZBrush** (siguió habiendo un solo
proceso, el que ya estaba abierto): antes se lanzaba `ZBrush.exe` con el script,
que importaba el modelo en *otra* ventana.

Falta la confirmación visual: la captura de pantalla no consiguió traer ZBrush al
frente (Windows bloquea el robo de foco), así que hay que mirar en ZBrush si el
subtool `GoBHandoffProbe` (cubo de 8v/6f) apareció en el tool abierto. Si
aparece, el arreglo está cerrado; si no, el helper no está llegando a la
instancia viva y toca la alternativa B: un observador del lado de ZBrush que
vigile `GoZ_ObjectList.txt`.
