# -*- coding: utf-8 -*-
import base64
import json
import logging
import uuid
import time
from odoo import models, fields, api, _
from odoo.exceptions import UserError

try:
    import pypdf
    import google.generativeai as genai
except ImportError:
    pypdf = None
    genai = None

_logger = logging.getLogger(__name__)

class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    # Campos personalizados para guardar el análisis de la IA
    x_ia_aprobado = fields.Boolean(string="Aprobado por IA", readonly=True, default=False)
    x_ia_score = fields.Float(string="Score de Coincidencia (%)", readonly=True)
    x_ia_analisis = fields.Text(string="Análisis de la IA", readonly=True)
    x_meeting_url = fields.Char(string="Enlace de Videollamada (Meet/Zoom)")
    
    # Campos de Puntuación Exacta (Matriz)
    x_score_tecnico = fields.Float(string="Puntaje Prueba Técnica (%)", readonly=True)
    x_score_psicometrico = fields.Float(string="Puntaje Prueba Psicométrica (%)", readonly=True)
    x_score_total = fields.Float(string="Score Final Combinado (%)", compute="_compute_score_total", store=True)
    x_ia_answer_ids = fields.One2many('hr.applicant.ia.answer', 'applicant_id', string="Evaluación de Preguntas Abiertas")

    # Campos de Agenda y Control Web para la Antesala
    x_token_acceso = fields.Char(string="Token de Acceso Único", readonly=True, copy=False)
    x_fecha_examen = fields.Datetime(string="Fecha y Hora del Examen", help="Zona horaria del servidor")
    x_url_antesala = fields.Char(string="Enlace de la Antesala Web", compute="_compute_url_antesala")
    
    x_is_new_stage = fields.Boolean(compute='_compute_stage_flags', string='Es Etapa Nuevo')
    x_is_finalist_stage = fields.Boolean(compute='_compute_stage_flags', string='Es Etapa Finalista')

    @api.depends('stage_id', 'stage_id.name')
    def _compute_stage_flags(self):
        for app in self:
            stage_name = app.stage_id.name.lower() if app.stage_id else ''
            app.x_is_new_stage = 'nuevo' in stage_name or 'new' in stage_name or 'initial' in stage_name or 'inici' in stage_name
            app.x_is_finalist_stage = 'finalist' in stage_name or 'final' in stage_name

    @api.depends('x_token_acceso')
    def _compute_url_antesala(self):
        """Genera la URL pública que se le enviará al candidato por correo."""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for applicant in self:
            if applicant.x_token_acceso:
                applicant.x_url_antesala = f"{base_url}/evaluacion/candidato/{applicant.x_token_acceso}"
            else:
                applicant.x_url_antesala = False

    @api.model_create_multi
    def create(self, vals_list):
        """Asegura que cada candidato nazca con un Token UUID único de seguridad."""
        for vals in vals_list:
            if not vals.get('x_token_acceso'):
                vals['x_token_acceso'] = str(uuid.uuid4())
        
        # 1. Crear el registro nativo en Odoo
        applicants = super(HrApplicant, self).create(vals_list)
        
        # Obtener la API Key de Gemini desde los parámetros del sistema de Odoo
        api_key = self.env['ir.config_parameter'].sudo().get_param('gemini.api_key')
        
        if not api_key or not genai:
            _logger.warning("Gemini API o librerías no configuradas. Se saltará el análisis de IA.")
            return applicants

        genai.configure(api_key=api_key)

        for applicant in applicants:
            # Buscar si el candidato tiene un CV adjunto
            attachment = self.env['ir.attachment'].search([
                ('res_model', '=', 'hr.applicant'),
                ('res_id', '=', applicant.id),
                ('mimetype', '=', 'application/pdf')
            ], limit=1)

            if attachment:
                # 2. Extraer texto del PDF
                cv_text = self._extract_text_from_pdf(attachment)
                if cv_text:
                    # 3. Analizar con Gemini
                    self._analyze_cv_with_gemini(applicant, cv_text)
                    
        return applicants

    def _extract_text_from_pdf(self, attachment):
        """Convierte el archivo binario del adjunto de Odoo en texto plano."""
        try:
            pdf_data = base64.b64decode(attachment.datas)
            # Guardar temporalmente en memoria para pypdf
            from io import BytesIO
            pdf_file = BytesIO(pdf_data)
            
            reader = pypdf.PdfReader(pdf_file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text
        except Exception as e:
            _logger.error(f"Error extrayendo texto del PDF: {str(e)}")
            return False

    def _analyze_cv_with_gemini(self, applicant, cv_text):
        """Construye el prompt, invoca a Gemini y procesa la decisión del filtro."""
        # Extraer las palabras clave configuradas en la Vacante (hr.job)
        # Se verifica si el campo existe en caso de que aún no se haya creado desde la interfaz.
        keywords = []
        if hasattr(applicant.job_id, 'x_keywords_ids'):
            keywords = [kw.name for kw in applicant.job_id.x_keywords_ids]
            
        keywords_str = ", ".join(keywords) if keywords else "Tecnologías generales de desarrollo"

        # Construcción del Prompt con formato de salida JSON estricto
        prompt = f"""
        Actúa como un reclutador experto en TI. Analiza el siguiente Currículum Vitae (CV) en texto plano
        y compáralo con las siguientes palabras clave requeridas para la vacante: [{keywords_str}].

        CV del Candidato:
        \"\"\"{cv_text}\"\"\"

        Debes responder ESTRICTAMENTE en formato JSON con la siguiente estructura (no agregues texto markdown fuera del JSON):
        {{
            "aprobado": true/false,
            "score_coincidencia": 85.5,
            "analisis_resumen": "El candidato cumple con el perfil debido a que maneja X tecnologías principales, aunque le falta experiencia en Y."
        }}
        """

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Invocar el modelo flash de Gemini 
                model = genai.GenerativeModel('gemini-3.5-flash')
                response = model.generate_content(
                    prompt,
                    generation_config={"response_mime_type": "application/json"}
                )
                
                # Parsear la respuesta estructurada
                result = json.loads(response.text)
                
                # Guardar los datos analíticos en la ficha del candidato
                applicant.write({
                    'x_ia_aprobado': result.get('aprobado', False),
                    'x_ia_score': result.get('score_coincidencia', 0.0),
                    'x_ia_analisis': result.get('analisis_resumen', '')
                })

                # 4. Acción automatizada basada en la decisión de la IA
                if result.get('aprobado'):
                    # Mover a la etapa de "Calificados" (Asegúrate de que el nombre o ID coincida en tu base de datos)
                    stage_calificado = self.env['hr.recruitment.stage'].search([('name', 'ilike', 'Calificados')], limit=1)
                    if stage_calificado:
                        applicant.write({'stage_id': stage_calificado.id})
                else:
                    # Buscar etapa "Rechazados" (Sensible a "Rechazad")
                    stage_rechazado = self.env['hr.recruitment.stage'].search([('name', 'ilike', 'Rechazad')], limit=1)
                    if stage_rechazado:
                        applicant.write({'stage_id': stage_rechazado.id, 'active': False})
                    else:
                        applicant.write({'active': False})
                    
                    # Enviar correo de rechazo automáticamente usando la plantilla XML
                    template = self.env.ref('hr_recruitment_ia.mail_template_applicant_rechazado', raise_if_not_found=False)
                    if template:
                        template.send_mail(applicant.id, force_send=False)
                break  # Salir del bucle si fue exitoso
            except Exception as e:
                if '429' in str(e) and attempt < max_retries - 1:
                    _logger.warning(f"Límite de cuota Gemini (429) alcanzado al analizar CV. Esperando 23 segundos antes del intento {attempt + 2}...")
                    time.sleep(23)
                else:
                    _logger.error(f"Error en la llamada a la API de Gemini tras {attempt + 1} intentos: {str(e)}")
                    # Guardar un mensaje claro de fallback en la ficha para que el usuario no se quede sin saber qué pasó
                    applicant.write({
                        'x_ia_analisis': f"Error al evaluar con Gemini API (Cuota excedida o servicio no disponible). Por favor, intenta presionar 'Analizar con IA' nuevamente en unos segundos.\n\nDetalle técnico: {str(e)}"
                    })
                    break

    def action_send_interview_invitation(self):
        """Envía manualmente la invitación usando la plantilla correcta según la etapa del Kanban."""
        for applicant in self:
            stage_name = applicant.stage_id.name.lower() if applicant.stage_id else ''
            
            if 'calificado' in stage_name:
                template = self.env.ref('hr_recruitment_ia.mail_template_applicant_calificado', raise_if_not_found=False)
            elif 'entrevista' in stage_name or 'técni' in stage_name or 'tecni' in stage_name:
                template = self.env.ref('hr_recruitment_ia.mail_template_applicant_prueba_tecnica', raise_if_not_found=False)
            elif 'psico' in stage_name:
                template = self.env.ref('hr_recruitment_ia.mail_template_applicant_prueba_psicometrica', raise_if_not_found=False)
            elif 'finalist' in stage_name or 'final' in stage_name:
                template = self.env.ref('hr_recruitment_ia.mail_template_applicant_entrevista_final', raise_if_not_found=False)
            else:
                # Fallback genérico a prueba técnica
                template = self.env.ref('hr_recruitment_ia.mail_template_applicant_prueba_tecnica', raise_if_not_found=False)
                
            if template:
                template.send_mail(applicant.id, force_send=False)
            
        # Retornar un efecto de recarga o mensaje flotante (rainbow man opcional)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Correo Enviado',
                'message': 'Se ha enviado la plantilla correcta según la etapa del candidato.',
                'sticky': False,
                'type': 'success',
            }
        }

    def action_seleccionar_ganador(self):
        """Mueve al candidato a Propuesta de Contrato y envía la oferta formal."""
        for applicant in self:
            # Buscar etapa de propuesta de contrato o contrato
            stage = self.env['hr.recruitment.stage'].search(['|', ('name', 'ilike', 'Contrat'), ('name', 'ilike', 'Contract')], limit=1)
            if stage:
                applicant.stage_id = stage.id
            
            template = self.env.ref('hr_recruitment_ia.mail_template_applicant_oferta_contrato', raise_if_not_found=False)
            if template:
                template.send_mail(applicant.id, force_send=False)
                
            applicant.message_post(body="<b style='color:green;'>🏆 Candidato Seleccionado:</b> Se ha movido a Propuesta de Contrato y enviado la oferta formal.")
            
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Oferta Enviada',
                'message': 'Se ha enviado la carta de oferta al candidato seleccionado.',
                'sticky': False,
                'type': 'success',
            }
        }

    def action_archivar_talent_pool(self):
        """Mueve al finalista a la reserva de talento y archiva la ficha conservando sus datos."""
        for applicant in self:
            template = self.env.ref('hr_recruitment_ia.mail_template_applicant_talent_pool', raise_if_not_found=False)
            if template:
                template.send_mail(applicant.id, force_send=False)
                
            applicant.message_post(body=f"<b style='color:#17a2b8;'>⭐ Archivo VIP en Talent Pool:</b> Finalista con {applicant.x_score_total:.1f}% de score transferido a reserva estratégica.")
            applicant.write({
                'active': False,
            })
            
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Talent Pool',
                'message': 'El finalista ha sido notificado y guardado en la reserva estratégica de talento.',
                'sticky': False,
                'type': 'info',
            }
        }

    def action_analizar_ia_manualmente(self):
        for applicant in self:
            api_key = self.env['ir.config_parameter'].sudo().get_param('gemini.api_key')
            if not api_key:
                raise UserError("Por favor, configura la API Key de Gemini en los Parámetros del Sistema (gemini.api_key).")
                
            if not genai:
                raise UserError("La librería google-generativeai no está instalada o cargada.")
                
            genai.configure(api_key=api_key)

            # Buscar el archivo PDF adjunto
            attachment = self.env['ir.attachment'].search([
                ('res_model', '=', 'hr.applicant'),
                ('res_id', '=', applicant.id),
                ('mimetype', '=', 'application/pdf')
            ], limit=1)

            if not attachment:
                raise UserError("No se encontró ningún archivo PDF adjunto en este candidato. Por favor, sube el currículum primero.")

            # Extraer texto del PDF
            cv_text = self._extract_text_from_pdf(attachment)
            if not cv_text:
                raise UserError("No se pudo extraer texto del PDF o el documento está vacío.")

            # Analizar con Gemini
            self._analyze_cv_with_gemini(applicant, cv_text)

    @api.depends('x_score_tecnico', 'x_score_psicometrico', 'x_ia_score')
    def _compute_score_total(self):
        for app in self:
            # Ejemplo de ponderación: Técnica 40%, Psicométrica 40%, IA 20%
            app.x_score_total = (app.x_score_tecnico * 0.4) + (app.x_score_psicometrico * 0.4) + (app.x_ia_score * 0.2)

    def _procesar_resultado_encuesta(self, user_input, score=None):
        """Lee el resultado del examen y aprueba/rechaza automáticamente."""
        if score is None:
            score = user_input.scoring_percentage or 0.0
            
        is_technical = (user_input.survey_id == self.job_id.x_encuesta_tecnica_id)
        is_psycho = (user_input.survey_id == self.job_id.x_encuesta_psicometrica_id)

        if not is_technical and not is_psycho:
            return  # No es una encuesta controlada por este flujo

        # Registrar puntajes SIEMPRE (tanto si aprueba como si reprueba)
        if is_technical:
            self.x_score_tecnico = score
            # Agrupar las justificaciones de la IA para que aparezcan en el correo de feedback
            ia_answers = self.x_ia_answer_ids.filtered(lambda a: a.score < a.max_score)
            if ia_answers:
                feedback = "\n\n".join([f"Pregunta: {a.question}\nObservación: {a.justification}" for a in ia_answers])
                self.x_ia_analisis = f"Observaciones de la evaluación técnica (Puntaje obtenido: {score}%):\n\n{feedback}"
        elif is_psycho:
            self.x_score_psicometrico = score
            self.x_ia_analisis = f"El resultado de la evaluación psicométrica ({score}%) no alcanzó el perfil mínimo requerido para la vacante."

        # Leer el porcentaje mínimo configurado dentro de la propia encuesta de Odoo (por defecto 70 si no está configurado)
        passing_score = user_input.survey_id.scoring_success_min if user_input.survey_id.scoring_type != 'no_scoring' else 70.0

        if score < passing_score:
            # RECHAZO AUTOMÁTICO
            stage_rechazado = self.env['hr.recruitment.stage'].search([('name', 'ilike', 'Rechazad')], limit=1)
            reason = "Prueba Técnica" if is_technical else "Prueba Psicométrica"
            
            # Registrar en bitácora
            self.message_post(body=f"<b style='color:red;'>Rechazo Automático:</b> No alcanzó el mínimo requerido de {passing_score}% en la {reason}. Obtuvo: {score}%")
            self.write({
                'active': False,
                'stage_id': stage_rechazado.id if stage_rechazado else self.stage_id.id,
            })
            
            # Enviar plantilla de rechazo
            template = self.env.ref('hr_recruitment_ia.mail_template_applicant_rechazado', raise_if_not_found=False)
            if template:
                template.send_mail(self.id, force_send=False)
        else:
            # APROBADO AUTOMÁTICO
            self.message_post(body=f"<b style='color:green;'>Aprobado en Encuesta:</b> Obtuvo {score}%. Avanzando de etapa.")
            
            if is_technical:
                # Mover a Psicométrica
                stage_psico = self.env['hr.recruitment.stage'].search(['|', ('name', 'ilike', 'Psicométrica'), ('name', 'ilike', 'Psicometrica')], limit=1)
                if stage_psico:
                    self.stage_id = stage_psico.id
            elif is_psycho:
                # Mover a Finalistas
                stage_final = self.env['hr.recruitment.stage'].search(['|', ('name', 'ilike', 'Finalist'), ('name', 'ilike', 'Final')], limit=1)
                if stage_final:
                    self.stage_id = stage_final.id

class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    @api.model_create_multi
    def create(self, vals_list):
        # Primero crear los adjuntos
        attachments = super(IrAttachment, self).create(vals_list)
        
        # Procesar si alguno es un CV de un candidato
        for attachment in attachments:
            if attachment.res_model == 'hr.applicant' and attachment.mimetype == 'application/pdf':
                applicant = self.env['hr.applicant'].browse(attachment.res_id)
                # Si el candidato existe y aún no ha sido analizado por IA
                if applicant.exists() and not applicant.x_ia_analisis:
                    try:
                        # Ejecutar el análisis automáticamente
                        applicant.action_analizar_ia_manualmente()
                    except Exception as e:
                        _logger.error(f"Error procesando IA automáticamente tras adjuntar CV: {str(e)}")
                        
        return attachments

class TalentPoolAddApplicants(models.TransientModel):
    _inherit = 'talent.pool.add.applicants'

    def _add_applicants_to_pool(self):
        talents = super()._add_applicants_to_pool()
        # Enviar el correo VIP a los candidatos originales que tengan correo
        active_ids = self.env.context.get('active_ids')
        if active_ids:
            applicants = self.env['hr.applicant'].browse(active_ids)
            for applicant in applicants:
                template = self.env.ref('hr_recruitment_ia.mail_template_applicant_talent_pool', raise_if_not_found=False)
                if template:
                    template.send_mail(applicant.id, force_send=False)
                applicant.message_post(body=f"<b style='color:#17a2b8;'>⭐ Archivo VIP en Talent Pool:</b> Finalista con {applicant.x_score_total:.1f}% de score transferido a reserva estratégica y notificado por correo.")
        return talents
