{
    'name': 'DTE El Salvador - Facturación POS',
    'version': '19.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Integración de Facturación Electrónica (DTE) para POS y Restaurante',
    'description': """
        Este módulo conecta el Punto de Venta de Odoo con la API del Ministerio de Hacienda de El Salvador
        para la emisión automática de Documentos Tributarios Electrónicos (DTE).
    """,
    'author': 'DTESV',
    'depends': [
        'base',
        'point_of_sale'
    ],
    'data': [
       'security/ir.model.access.csv',
       'views/res_config_settings_views.xml',
       'data/cron_dte.xml',
       'views/pos_order_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_dte_sv/static/src/xml/order_receipt.xml',
            'pos_dte_sv/static/src/js/pos_tip.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}