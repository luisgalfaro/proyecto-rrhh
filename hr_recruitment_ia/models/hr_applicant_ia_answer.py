# -*- coding: utf-8 -*-
from odoo import models, fields

class HrApplicantIaAnswer(models.Model):
    _name = 'hr.applicant.ia.answer'
    _description = 'Detalle de Evaluación IA para Preguntas Abiertas'

    applicant_id = fields.Many2one('hr.applicant', string="Candidato", ondelete='cascade')
    user_input_line_id = fields.Many2one('survey.user_input.line', string="Línea de Encuesta", ondelete='cascade')
    question = fields.Char(string="Pregunta", readonly=True)
    answer = fields.Text(string="Respuesta del Candidato", readonly=True)
    expected_criteria = fields.Text(string="Criterio Esperado", readonly=True)
    score = fields.Float(string="Puntaje IA / Manual")
    max_score = fields.Float(string="Puntaje Máximo", readonly=True)
    justification = fields.Text(string="Análisis y Justificación IA")

    def write(self, vals):
        res = super(HrApplicantIaAnswer, self).write(vals)
        if 'score' in vals:
            for record in self:
                if record.user_input_line_id:
                    record.user_input_line_id.sudo().write({'answer_score': record.score})
                    # Disparar recálculo del puntaje total en el candidato
                    user_input = record.user_input_line_id.user_input_id
                    if user_input and record.applicant_id:
                        record.applicant_id.sudo()._procesar_resultado_encuesta(user_input)
                        user_input._recalculate_combined_score(record.applicant_id)
        return res
