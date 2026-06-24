# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import datetime

class RecruitmentWebsiteController(http.Controller):

    @http.route('/evaluacion/candidato/<string:token>', type='http', auth='public', website=True)
    def antesala_evaluacion(self, token, **kwargs):
        """Renderiza la antesala web del candidato controlando el acceso y los tiempos."""
        # Buscar al candidato mediante el Token UUID de seguridad
        applicant = request.env['hr.applicant'].sudo().search([('x_token_acceso', '=', token)], limit=1)
        
        if not applicant:
            return request.render('website.404')  # Token inválido

        ahora = datetime.now()
        fecha_examen = applicant.x_fecha_examen
        
        # Determinar cuál examen le corresponde realizar según su etapa actual en el Kanban
        encuesta_id = False
        stage_name = applicant.stage_id.name.lower() if applicant.stage_id else ''
        
        if 'técni' in stage_name or 'tecni' in stage_name or 'entrevista' in stage_name:
            encuesta_id = applicant.job_id.x_encuesta_tecnica_id
        elif 'psico' in stage_name:
            encuesta_id = applicant.job_id.x_encuesta_psicometrica_id

        # Si no hay examen asignado o la etapa no corresponde a evaluaciones
        if not encuesta_id:
            return request.render('website.404')

        # === AUTORIZACIÓN MÁGICA: Crear el acceso al examen en Odoo si no existe ===
        # Para evitar el error de "Un token de acceso debe ser único", creamos un token compuesto (TokenCandidato-EncuestaID)
        token_encuesta = f"{applicant.x_token_acceso}-{encuesta_id.id}"
        
        user_input = request.env['survey.user_input'].sudo().search([
            ('survey_id', '=', encuesta_id.id),
            ('access_token', '=', token_encuesta)
        ], limit=1)

        if not user_input:
            user_input = request.env['survey.user_input'].sudo().create({
                'survey_id': encuesta_id.id,
                'partner_id': applicant.partner_id.id if applicant.partner_id else False,
                'email': applicant.email_from,
                'nickname': applicant.partner_name or applicant.name,
                'access_token': token_encuesta,
                'invite_token': token_encuesta,
                'state': 'new',
            })

        # Obtener la URL nativa de Odoo para iniciar esa encuesta específica
        url_encuesta = f"/survey/start/{encuesta_id.access_token}?answer_token={token_encuesta}"

        # Pasar los datos formateados a la plantilla QWeb de la antesala
        values = {
            'applicant': applicant,
            'url_encuesta': url_encuesta,
            'link_reunion': applicant.x_meeting_url or '#',
            'fecha_examen_iso': applicant.x_fecha_examen.isoformat() + 'Z' if applicant.x_fecha_examen else '',
        }
        return request.render('hr_recruitment_ia.antesala_web_template', values)
