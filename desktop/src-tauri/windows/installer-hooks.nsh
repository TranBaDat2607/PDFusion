; NSIS installer hooks, wired in through tauri.conf.json's
; bundle.windows.nsis.installerHooks.
;
; They register PDFusion under Explorer's "Open with" for .pdf, and deliberately
; nothing more (#30).
;
; Tauri's own `bundle.fileAssociations` is not used for this. Its APP_ASSOCIATE
; macro overwrites the default value of Software\Classes\.pdf. On a machine
; where the user never explicitly picked a PDF app, that makes PDFusion the
; default reader for every PDF. PDFusion is a translator, not a reader, so it
; adds itself to the list (OpenWithProgids and Applications\PDFusion.exe) and
; never changes which app a double-click opens.
;
; SHCTX is HKCU here, because bundle.windows.nsis.installMode is "currentUser".
; The chosen file arrives as argv[1]: through `initial_file_argument` on a first
; launch, and through the single-instance handoff (`pdfusion://open-file`) when
; the app is already running (lib.rs).

!define PDFUSION_PROGID "PDFusion.pdf"

!macro NSIS_HOOK_PREINSTALL
!macroend

!macro NSIS_HOOK_POSTINSTALL
  WriteRegStr SHCTX "Software\Classes\${PDFUSION_PROGID}" "" "PDF document"
  WriteRegStr SHCTX "Software\Classes\${PDFUSION_PROGID}\DefaultIcon" "" "$INSTDIR\${MAINBINARYNAME}.exe,0"
  WriteRegStr SHCTX "Software\Classes\${PDFUSION_PROGID}\shell\open" "FriendlyAppName" "${PRODUCTNAME}"
  WriteRegStr SHCTX "Software\Classes\${PDFUSION_PROGID}\shell\open\command" "" '"$INSTDIR\${MAINBINARYNAME}.exe" "%1"'
  WriteRegStr SHCTX "Software\Classes\.pdf\OpenWithProgids" "${PDFUSION_PROGID}" ""

  WriteRegStr SHCTX "Software\Classes\Applications\${MAINBINARYNAME}.exe" "FriendlyAppName" "${PRODUCTNAME}"
  WriteRegStr SHCTX "Software\Classes\Applications\${MAINBINARYNAME}.exe\SupportedTypes" ".pdf" ""
  WriteRegStr SHCTX "Software\Classes\Applications\${MAINBINARYNAME}.exe\shell\open\command" "" '"$INSTDIR\${MAINBINARYNAME}.exe" "%1"'

  ; SHCNE_ASSOCCHANGED, so Explorer's Open with list updates without a sign-out.
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; Exactly what POSTINSTALL wrote. The .pdf key itself belongs to Windows and
  ; to whichever app is the default, so only PDFusion's value comes out of it.
  DeleteRegValue SHCTX "Software\Classes\.pdf\OpenWithProgids" "${PDFUSION_PROGID}"
  DeleteRegKey SHCTX "Software\Classes\${PDFUSION_PROGID}"
  DeleteRegKey SHCTX "Software\Classes\Applications\${MAINBINARYNAME}.exe"

  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend
