# -*- coding: utf-8 -*-
from odoo import models, fields

class HrJobKeyword(models.Model):
    _name = 'hr.job.keyword'
    _description = 'Palabras Clave de IA'

    name = fields.Char(string='Palabra Clave', required=True)
    color = fields.Integer(string='Color')

class HrJob(models.Model):
    _inherit = 'hr.job'

    x_keywords_ids = fields.Many2many(
        'hr.job.keyword',
        string='Palabras Clave para la IA',
        help='Añade las tecnologías o habilidades que la IA debe buscar en el CV del candidato.'
    )
    x_encuesta_tecnica_id = fields.Many2one('survey.survey', string='Examen Técnico', help='Encuesta para la etapa de Prueba Técnica')
    x_encuesta_psicometrica_id = fields.Many2one('survey.survey', string='Examen Psicométrico', help='Encuesta para la etapa de Prueba Psicométrica')
