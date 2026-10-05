include(FindGettext)

set(DOMAIN ${FULL_PROJECT_NAME})
set(POT_FILE ${DOMAIN}.pot)
file(GLOB PO_FILES *.po)

# Recetario no tiene traducciones (todo el texto va en el HTML y en
# Python, y no usa tr()/qsTr()). El target del .pot se mantenía por
# costumbre de la plantilla, pero here se rompía: xgettext con
# --c++ --qt --language=javascript a la vez no genera nada (son
# lenguajes excluyentes) y el "copy" posterior abortaba la
# compilación entera. Se deja un target vacío que además crea un
# .pot válido por si algún día se quiere usar.
add_custom_target(${POT_FILE} ALL
    COMMENT "Recetario: sin traducciones, se omite el .pot"
    COMMAND ${CMAKE_COMMAND} -E touch ${CMAKE_CURRENT_BINARY_DIR}/${POT_FILE}
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${CMAKE_CURRENT_BINARY_DIR}/${POT_FILE} ${CMAKE_CURRENT_SOURCE_DIR}/${POT_FILE})

# Builds the binary translations catalog for each language
# it finds source translations (*.po) for
foreach(PO_FILE ${PO_FILES})
    get_filename_component(LANG ${PO_FILE} NAME_WE)
    gettext_process_po_files(${LANG} ALL PO_FILES ${PO_FILE})
    set(INSTALL_DIR ${CMAKE_INSTALL_LOCALEDIR}/share/locale/${LANG}/LC_MESSAGES)
    install(FILES ${CMAKE_CURRENT_BINARY_DIR}/${LANG}.gmo
            DESTINATION ${INSTALL_DIR}
            RENAME ${DOMAIN}.mo)
endforeach(PO_FILE)