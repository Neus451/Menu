/*
 * Copyright (C) 2026  Your FullName
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, version 3.
 *
 * appname is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 *  along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

import QtQuick 2.7
import Lomiri.Components 1.3
import QtWebView 1.1
import io.thp.pyotherside 1.4

MainView {
    id: root
    objectName: 'mainView'
    applicationName: 'appname.neus'

    width: units.gu(45)
    height: units.gu(75)

    // ---- Lo que ve el usuario si algo falla (por ejemplo, el servidor). ----
    Column {
        id: errorBox
        anchors {
            left: parent.left
            right: parent.right
            top: parent.top
            margins: units.gu(2)
        }
        spacing: units.gu(1)
        visible: false

        Label {
            text: "Recetario no ha podido arrancar"
            font.bold: true
            font.pointSize: units.gu(2.2)
            wrapMode: Text.WordWrap
            width: parent.width
        }
        Label {
            id: errorText
            text: ""
            wrapMode: Text.WordWrap
            width: parent.width
            color: "#a94442"
        }
        Button {
            text: "Reintentar"
            onClicked: {
                errorBox.visible = false
                webview.url = ""
                startRecetario()
            }
        }
    }

    function startRecetario() {
        attempts = 0
        webview.url = ""
        python.call('recetario_app.start', [], function(url) {
            console.log('Recetario listo en ' + url);
            attempts = 0;
            webview.url = url;
        });
    }

    property int attempts: 0

    WebView {
        id: webview
        anchors.fill: parent
        visible: !errorBox.visible

        // El servidor tarda un momento en levantar: reintenta unas veces y,
        // si de verdad no responde, enséñale el error a la persona.
        Timer {
            id: retryTimer
            interval: 700
            repeat: false
            onTriggered: {
                if (webview.loadRequest.status === WebView.LoadFailedStatus)
                    retry();
            }
        }

        function retry() {
            if (attempts < 20) {
                attempts++;
                console.log('Reintento ' + attempts + ' de 20');
                retryTimer.restart();
                webview.url = "http://127.0.0.1:8765/";
            } else {
                errorBox.visible = true;
                errorText.text = "El servidor interno (127.0.0.1:8765) no responde.\n\n"
                    + "Comprueba que la app tiene permiso de red y de WebView:\n"
                    + "Ajustes → Permisos de las aplicaciones → Recetario.";
            }
        }

        onLoadingChanged: {
            if (loadRequest.status === WebView.LoadSucceededStatus)
                attempts = 0;
            else if (loadRequest.status === WebView.LoadFailedStatus)
                retryTimer.restart();
        }
    }

    Python {
        id: python

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl('../src/'));

            importModule('recetario_app', function() {
                // start() devuelve la URL (o un texto de error) para poder
                // enseñarlo en pantalla en vez de fallar en silencio.
                python.call('recetario_app.start', [], function(resultado) {
                    if (typeof resultado === 'string' && resultado.indexOf('http') === 0) {
                        console.log('Recetario listo en ' + resultado);
                        webview.url = resultado;
                    } else {
                        errorBox.visible = true;
                        errorText.text = resultado || "No se pudo arrancar el servidor.";
                    }
                });
            }, function() {
                // El módulo no se pudo cargar: casi siempre es Python/PyOtherSide.
                errorBox.visible = true;
                errorText.text = "No se pudo cargar el módulo Python.\n"
                    + "Comprueba que PyOtherSide y python3 están instalados en la app.";
            });
        }

        onError: {
            console.log('python error: ' + traceback);
            errorBox.visible = true;
            errorText.text = "Error de Python:\n" + (traceback || "(sin detalle)");
        }
    }
}