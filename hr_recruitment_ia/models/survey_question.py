# -*- coding: utf-8 -*-
from odoo import models, fields

class SurveyQuestion(models.Model):
    _inherit = 'survey.question'

    x_ia_max_score = fields.Float(string="Puntaje Máximo IA", help="Puntos máximos que la Inteligencia Artificial puede otorgar a esta respuesta abierta.", default=0.0)
    x_ia_expected_answer = fields.Text(string="Criterio de Evaluación (IA)", help="Describe brevemente qué debe contener la respuesta para obtener la nota máxima.")
