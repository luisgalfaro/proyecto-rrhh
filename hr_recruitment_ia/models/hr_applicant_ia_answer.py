# -*- coding: utf-8 -*-
from odoo import models, fields

class HrApplicantIaAnswer(models.Model):
    _name = 'hr.applicant.ia.answer'
    _description = 'Detalle de Evaluación IA para Preguntas Abiertas'

    applicant_id = fields.Many2one('hr.applicant', string="Candidato", ondelete='cascade')
    question = fields.Char(string="Pregunta", readonly=True)
    answer = fields.Text(string="Respuesta del Candidato", readonly=True)
    expected_criteria = fields.Text(string="Criterio Esperado", readonly=True)
    score = fields.Float(string="Puntaje IA", readonly=True)
    max_score = fields.Float(string="Puntaje Máximo", readonly=True)
    justification = fields.Text(string="Análisis y Justificación IA", readonly=True)
