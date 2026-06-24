import random
import string
import logging
import requests
import json
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class ResPartner(models.Model):
    _inherit = 'res.partner'

    nrc = fields.Char(string="NRC", help="Número de Registro de Contribuyente")
    actividad_economica = fields.Char(string="Código de Actividad Económica", default="10005", help="Código de Giro según MH")


class PosOrder(models.Model):
    _inherit = 'pos.order'

    dte_status = fields.Selection([
        ('pendiente', 'Pendiente'),
        ('exito', 'Autorizado MH'),
        ('rechazado', 'Rechazado MH'),
        ('contingencia', 'Contingencia'),
        ('anulado', 'Anulado / Invalidado')
    ], string="Estado DTE", default='pendiente', readonly=True, copy=False)
    
    sello_recepcion = fields.Char(string="Sello de Recepción MH", readonly=True, copy=False)
    codigo_generacion = fields.Char(string="Código de Generación (UUID)", readonly=True, copy=False)
    middleware_id = fields.Integer(string="ID Factura Middleware", readonly=True, copy=False)

    def _clean_documento(self, doc_original):
        if not doc_original:
            return ""
        doc_limpio = "".join(c for c in doc_original if c.isalnum())
        if not doc_limpio.strip('0'):
            return ""
        return doc_limpio

    def _get_dte_token(self, force_refresh=False):
        config_param = self.env['ir.config_parameter'].sudo()
        cached_token = config_param.get_param('pos_dte_sv.access_token', default='')

        if cached_token and not force_refresh:
            return cached_token

        api_url_base = (config_param.get_param('pos_dte_sv.api_url', default='') or config_param.get_param('pos_dte_sv.dte_api_url', default='')).rstrip('/')
        correo_login = config_param.get_param('pos_dte_sv.usuario', default='') or config_param.get_param('pos_dte_sv.dte_usuario', default='')
        password_login = config_param.get_param('pos_dte_sv.password', default='') or config_param.get_param('pos_dte_sv.dte_password', default='')

        if not api_url_base:
            _logger.error("DTE El Salvador: No se configuró la URL Base de la API.")
            return ""

        url_login = f"{api_url_base}/auth/login"
        try:
            _logger.info(f"DTE El Salvador: Intentando login en {url_login} con usuario: {correo_login}")
            res_login = requests.post(url_login, json={"correo": correo_login, "password": password_login}, timeout=10)
            _logger.info(f"DTE El Salvador: Respuesta login: Código {res_login.status_code}, Body: {res_login.text}")
            if res_login.status_code == 200:
                access_token = res_login.json().get("access_token", "")
                if access_token:
                    _logger.info("DTE El Salvador: Token obtenido correctamente.")
                    config_param.set_param('pos_dte_sv.access_token', access_token)
                    return access_token
                else:
                    _logger.warning("DTE El Salvador: Respuesta 200 pero no se encontró access_token en el JSON.")
        except Exception as e:
            _logger.error(f"DTE El Salvador: Error de conexión en Login: {str(e)}")
        
        _logger.warning("DTE El Salvador: No se pudo obtener el token. Devolviendo vacío.")
        return ""

    @api.model
    def _process_order(self, order, draft, *args, **kwargs):
        res = super(PosOrder, self)._process_order(order, draft, *args, **kwargs)
        pos_order = self.browse(res)

        if pos_order:
            config_param = self.env['ir.config_parameter'].sudo()
            modo_simulacion = config_param.get_param('pos_dte_sv.modo_simulacion', default=False) or config_param.get_param('pos_dte_sv.dte_modo_simulacion', default=False)
            es_simulado = str(modo_simulacion).lower() in ['true', '1', 'yes']

            if es_simulado:
                random_string = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                pos_order.write({
                    'dte_status': 'exito',
                    'sello_recepcion': f"2026DTE-{random_string}-MH",
                    'codigo_generacion': f"345F{random_string}-7EC0-426D-AF44-B2ECE6E74150"
                })
            else:
                api_url_base = (config_param.get_param('pos_dte_sv.api_url', default='') or config_param.get_param('pos_dte_sv.dte_api_url', default='')).rstrip('/')
                if not api_url_base:
                    pos_order.write({'dte_status': 'pendiente'})
                    return res

                access_token = self._get_dte_token()
                if not access_token:
                    pos_order.write({'dte_status': 'contingencia'})
                    return res

                # ==========================================
                # DETECTAR SI ES REEMBOLSO O VENTA NORMAL
                # ==========================================
                es_reembolso = pos_order.amount_total < 0
                cliente = pos_order.partner_id
                
                if es_reembolso:
                    tipo_documento = "05" # Nota de Crédito
                else:
                    es_credito_fiscal = bool(cliente and cliente.nrc)
                    tipo_documento = "03" if es_credito_fiscal else "01"

                doc_limpio = pos_order._clean_documento(cliente.vat) if cliente else ""
                cliente_documento = doc_limpio if doc_limpio else None

                # Excluir la propina (con código 'PROPINA') de la sumatoria del total a pagar en el DTE
                tip_amount = sum(line.price_subtotal_incl for line in pos_order.lines if line.product_id.default_code == 'PROPINA')
                dte_total_pagar = abs(pos_order.amount_total) - abs(tip_amount)

                payload_emision = {
                    "tipo_documento": tipo_documento,
                    "cliente_nombre": cliente.name if cliente else "Consumidor Final",
                    "cliente_documento": cliente_documento,
                    "cliente_nrc": cliente.nrc if (cliente and not es_reembolso and cliente.nrc) else None,
                    "cliente_actividad": cliente.actividad_economica if (cliente and not es_reembolso and cliente.nrc) else "10005",
                    "cliente_correo": cliente.email if cliente else "",
                    "total_pagar": round(dte_total_pagar, 2), # Enviamos valor absoluto positivo sin propina
                    "items": []
                }

                # Si es Nota de Crédito (05), vinculamos los datos del documento original
                if es_reembolso and pos_order.refunded_order_ids:
                    orden_original = pos_order.refunded_order_ids[0]
                    if orden_original.codigo_generacion:
                        payload_emision["doc_relacionado_uuid"] = orden_original.codigo_generacion
                        payload_emision["doc_relacionado_fecha"] = orden_original.date_order.strftime('%Y-%m-%d')

                for line in pos_order.lines:
                    if line.product_id.default_code == 'PROPINA':
                        continue # Excluir la propina del desglose de items en el DTE
                    payload_emision["items"].append({
                        "cantidad": abs(line.qty),
                        "codigo": line.product_id.default_code or "P000",
                        "descripcion": line.product_id.name,
                        "precio_unitario": abs(line.price_unit),
                        "descuento": abs((line.price_unit * line.qty) - line.price_subtotal_incl)
                    })

                url_emitir = f"{api_url_base}/hacienda/emitir"
                headers_emision = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}

                try:
                    _logger.info(f"DTE El Salvador: Enviando DTE a {url_emitir}. Payload: {json.dumps(payload_emision)}")
                    res_emision = requests.post(url_emitir, json=payload_emision, headers=headers_emision, timeout=15)
                    _logger.info(f"DTE El Salvador: Respuesta de emisión (Intento 1): Código {res_emision.status_code}, Body: {res_emision.text}")
                    
                    if res_emision.status_code == 401:
                        _logger.info("DTE El Salvador: Posible token expirado (401). Renovando token...")
                        access_token = self._get_dte_token(force_refresh=True)
                        if access_token:
                            headers_emision["Authorization"] = f"Bearer {access_token}"
                            res_emision = requests.post(url_emitir, json=payload_emision, headers=headers_emision, timeout=15)
                            _logger.info(f"DTE El Salvador: Respuesta de emisión (Intento 2 tras renovar token): Código {res_emision.status_code}, Body: {res_emision.text}")
                    
                    if res_emision.status_code == 200:
                        datos_respuesta = res_emision.json()
                        _logger.info(f"DTE El Salvador: Datos decodificados de la respuesta: {datos_respuesta}")
                        
                        if datos_respuesta.get("status") == "success":
                            _logger.info("DTE El Salvador: Emisión exitosa. Guardando sellos.")
                            pos_order.write({
                                'dte_status': 'exito',
                                'sello_recepcion': datos_respuesta.get("sello_recepcion"),
                                'codigo_generacion': datos_respuesta.get("codigo_generacion"),
                                'middleware_id': datos_respuesta.get("id") or datos_respuesta.get("factura_id", 0)
                            })
                        elif datos_respuesta.get("status") == "rechazado":
                            _logger.warning("DTE El Salvador: La API indica que el documento fue rechazado.")
                            pos_order.write({'dte_status': 'rechazado'})
                        else:
                            _logger.warning(f"DTE El Salvador: Estado no reconocido ({datos_respuesta.get('status')}). Mandando a contingencia.")
                            pos_order.write({'dte_status': 'contingencia'})
                    else:
                        _logger.warning(f"DTE El Salvador: La API no devolvió 200 OK. Código: {res_emision.status_code}. Mandando a contingencia.")
                        pos_order.write({'dte_status': 'contingencia'})
                except Exception as e:
                    pos_order.write({'dte_status': 'contingencia'})
                    _logger.error(f"DTE El Salvador: Error de red o conexión al emitir DTE: {str(e)}")

        return res

    def action_invalidar_dte_wizard(self):
        """ Abre el asistente para invalidar el DTE actual """
        self.ensure_one()
        if self.dte_status != 'exito':
            raise UserError("Solo se pueden invalidar documentos autorizados con éxito por Hacienda.")
        
        return {
            'name': 'Invalidar Documento Electrónico (DTE)',
            'type': 'ir.actions.act_window',
            'res_model': 'pos.order.invalidar.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_pos_order_id': self.id}
        }


    @api.model
    def cron_enviar_contingencias(self):
        """ Método llamado por el cron (ir.cron) para reintentar órdenes en contingencia """
        _logger.info("Iniciando cron para enviar DTEs en contingencia...")
        ordenes = self.search([('dte_status', '=', 'contingencia')])
        
        if not ordenes:
            _logger.info("No hay DTEs en contingencia para procesar.")
            return

        for order in ordenes:
            try:
                # Reutilizamos la lógica del token
                access_token = self._get_dte_token()
                if not access_token:
                    continue

                config_param = self.env['ir.config_parameter'].sudo()
                api_url_base = (config_param.get_param('pos_dte_sv.api_url', default='') or config_param.get_param('pos_dte_sv.dte_api_url', default='')).rstrip('/')
                
                es_reembolso = order.amount_total < 0
                cliente = order.partner_id
                
                if es_reembolso:
                    tipo_documento = "05"
                else:
                    es_credito_fiscal = bool(cliente and cliente.nrc)
                    tipo_documento = "03" if es_credito_fiscal else "01"

                doc_limpio = order._clean_documento(cliente.vat) if cliente else ""
                cliente_documento = doc_limpio if doc_limpio else None

                # Excluir la propina (con código 'PROPINA') de la sumatoria del total a pagar en el DTE
                tip_amount = sum(line.price_subtotal_incl for line in order.lines if line.product_id.default_code == 'PROPINA')
                dte_total_pagar = abs(order.amount_total) - abs(tip_amount)

                payload_emision = {
                    "tipo_documento": tipo_documento,
                    "cliente_nombre": cliente.name if cliente else "Consumidor Final",
                    "cliente_documento": cliente_documento,
                    "cliente_nrc": cliente.nrc if (cliente and not es_reembolso and cliente.nrc) else None,
                    "cliente_actividad": cliente.actividad_economica if (cliente and not es_reembolso and cliente.nrc) else "10005",
                    "cliente_correo": cliente.email if cliente else "",
                    "total_pagar": round(dte_total_pagar, 2), # Enviamos valor absoluto positivo sin propina
                    "items": []
                }

                if es_reembolso and order.refunded_order_ids:
                    orden_original = order.refunded_order_ids[0]
                    if orden_original.codigo_generacion:
                        payload_emision["doc_relacionado_uuid"] = orden_original.codigo_generacion
                        payload_emision["doc_relacionado_fecha"] = orden_original.date_order.strftime('%Y-%m-%d')

                for line in order.lines:
                    if line.product_id.default_code == 'PROPINA':
                        continue # Excluir la propina del desglose de items en el DTE
                    payload_emision["items"].append({
                        "cantidad": abs(line.qty),
                        "codigo": line.product_id.default_code or "P000",
                        "descripcion": line.product_id.name,
                        "precio_unitario": abs(line.price_unit),
                        "descuento": abs((line.price_unit * line.qty) - line.price_subtotal_incl)
                    })

                url_emitir = f"{api_url_base}/hacienda/emitir"
                headers_emision = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}

                res_emision = requests.post(url_emitir, json=payload_emision, headers=headers_emision, timeout=15)
                
                if res_emision.status_code == 200:
                    datos_respuesta = res_emision.json()
                    if datos_respuesta.get("status") == "success":
                        order.write({
                            'dte_status': 'exito',
                            'sello_recepcion': datos_respuesta.get("sello_recepcion"),
                            'codigo_generacion': datos_respuesta.get("codigo_generacion"),
                            'middleware_id': datos_respuesta.get("id") or datos_respuesta.get("factura_id", 0)
                        })
                        self.env.cr.commit()  # Asegurar de guardar en caso de error en la siguiente
                    elif datos_respuesta.get("status") == "rechazado":
                        order.write({'dte_status': 'rechazado'})
                        self.env.cr.commit()
            except Exception as e:
                _logger.error(f"DTE El Salvador: Error en cron al procesar orden {order.id}: {str(e)}")

class PosOrderInvalidarWizard(models.TransientModel):
    """ Asistente técnico para procesar la invalidación/anulación ante el MH """
    _name = 'pos.order.invalidar.wizard'
    _description = 'Asistente de Invalidación DTE'

    pos_order_id = fields.Many2one('pos.order', string="Orden de Venta Affected", required=True)
    tipo_invalidacion = fields.Selection([
        ('1', '1 - Error en información'),
        ('2', '2 - Rescindir operación'),
        ('3', '3 - Otro')
    ], string="Tipo de Invalidación (CAT-024)", default='2', required=True)
    
    motivo_anulacion = fields.Text(string="Motivo de Anulación", required=True, placeholder="Ej. Cliente solicita rescindir el servicio adquirido.")
    nombre_responsable = fields.Char(string="Nombre del Responsable Emisor", required=True)
    doc_identidad_responsable = fields.Char(string="DUI o NIT del Responsable", required=True)
    codigo_generacion_reemplazo = fields.Char(string="Código Generación Reemplazo", help="Obligatorio si el tipo es 1 o 3")

    def action_procesar_invalidacion(self):
        self.ensure_one()
        order = self.pos_order_id

        # Validaciones previas según normativa salvadoreña
        if self.tipo_invalidacion in ['1', '3'] and not self.codigo_generacion_reemplazo:
            raise UserError("Para los tipos de invalidación 1 y 3, es obligatorio proveer el Código de Generación de la nueva factura de reemplazo.")

        config_param = self.env['ir.config_parameter'].sudo()
        api_url_base = (config_param.get_param('pos_dte_sv.api_url', default='') or config_param.get_param('pos_dte_sv.dte_api_url', default='')).rstrip('/')
        
        access_token = order._get_dte_token()
        if not api_url_base or not access_token:
            raise UserError("No se pudo establecer conexión o autenticación con el Middleware.")

        # Construir payload según la documentación de tu middleware
        payload = {
            "factura_id": order.middleware_id if order.middleware_id else order.id,
            "tipo_invalidacion": int(self.tipo_invalidacion),
            "motivo_anulacion": self.motivo_anulacion,
            "nombre_responsable": self.nombre_responsable,
            "doc_identidad_responsable": order._clean_documento(self.doc_identidad_responsable),
            "codigo_generacion_reemplazo": self.codigo_generacion_reemplazo or None
        }

        url_invalidar = f"{api_url_base}/hacienda/invalidar"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}

        try:
            res = requests.post(url_invalidar, json=payload, headers=headers, timeout=15)
            if res.status_code == 200:
                datos = res.json()
                if datos.get("status") == "success":
                    order.write({
                        'dte_status': 'anulado',
                        'sello_recepcion': f"ANULADO - {datos.get('sello_anulacion', '')}"
                    })
                    return {'type': 'ir.actions.act_window_close'}
                else:
                    raise UserError(f"El Middleware rechazó la anulación: {datos.get('mensaje')}")
            else:
                raise UserError(f"Error de comunicación con el servidor de la API. Código de estado: {res.status_code}")
        except Exception as e:
            raise UserError(f"Error crítico al conectar con la API de Invalidación: {str(e)}")