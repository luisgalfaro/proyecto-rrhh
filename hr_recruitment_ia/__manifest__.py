# -*- coding: utf-8 -*-
{
    'name': 'AI Recruitment (Gemini)',
    'version': '1.0',
    'summary': 'Automatización e Integración de IA para CVs con Gemini API',
    'description': """
        Integra la API de Google Gemini para analizar automáticamente
        los currículums (PDF) de los postulantes y validar si cumplen
        con los requisitos del puesto.
    """,
    'category': 'Human Resources/Recruitment',
    'author': 'Antigravity',
    'depends': ['hr_recruitment', 'website', 'survey'],
    'data': [
        'security/ir.model.access.csv',
        'data/mail_template_data.xml',
        'views/hr_applicant_views.xml',
        'views/hr_applicant_matrix_views.xml',
        'views/hr_job_views.xml',
        'views/survey_question_views.xml',
        'views/templates.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
