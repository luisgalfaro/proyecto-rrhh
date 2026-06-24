from odoo import models, fields

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dte_api_url = fields.Char(
        string="URL Base de la API DTE", 
        config_parameter='pos_dte_sv.api_url', 
        default="http://localhost:8000/api/v1"
    )
    dte_usuario = fields.Char(
        string="Correo Empresa (API)", 
        config_parameter='pos_dte_sv.usuario'
    )
    dte_password = fields.Char(
        string="Contraseña API", 
        config_parameter='pos_dte_sv.password'
    )
    dte_modo_simulacion = fields.Boolean(
        string="Modo Simulación (Sin API real)", 
        config_parameter='pos_dte_sv.modo_simulacion',
        default=False
    )