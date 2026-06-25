import json
import logging
from odoo import models, api

_logger = logging.getLogger(__name__)

class SurveyUserInput(models.Model):
    _inherit = 'survey.user_input'

    def write(self, vals):
        # Primero ejecutar la escritura normal
        res = super(SurveyUserInput, self).write(vals)
        
        # Si el estado de la encuesta cambió a 'done' (finalizada)
        if vals.get('state') == 'done':
            for user_input in self:
                if user_input.access_token:
                    # El access_token es compuesto: x_token_acceso-encuesta_id. Extraemos la parte del candidato.
                    token_candidato = user_input.access_token.rsplit('-', 1)[0]
                    applicant = self.env['hr.applicant'].sudo().search([('x_token_acceso', '=', token_candidato)], limit=1)
                    if not applicant:
                        # Fallback por si era una encuesta creada con el token antiguo sin sufijo
                        applicant = self.env['hr.applicant'].sudo().search([('x_token_acceso', '=', user_input.access_token)], limit=1)
                        
                    if applicant:
                        # Evaluar las abiertas y luego procesar todo
                        self._evaluate_open_questions_with_ia(user_input, applicant)
        return res

    def _evaluate_open_questions_with_ia(self, user_input, applicant):
        user_input = user_input.sudo()
        applicant = applicant.sudo()
        
        try:
            import google.generativeai as genai
        except ImportError:
            applicant.message_post(body="<b style='color:orange;'>Aviso IA:</b> No se pudo importar google.generativeai para evaluar preguntas abiertas.")
            applicant._procesar_resultado_encuesta(user_input)
            return
            
        api_key = self.env['ir.config_parameter'].sudo().get_param('gemini.api_key')
        if not api_key:
            applicant.message_post(body="<b style='color:orange;'>Aviso IA:</b> No se encontró la API Key de Gemini (gemini.api_key) para evaluar preguntas abiertas.")
            applicant._procesar_resultado_encuesta(user_input)
            return
            
        genai.configure(api_key=api_key)
        
        # Obtener dinámicamente la lista de modelos compatibles disponibles en esta cuenta/SDK
        available_models = []
        try:
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    model_name = m.name.replace('models/', '')
                    available_models.append(model_name)
        except Exception as e:
            _logger.warning(f"No se pudo obtener lista de modelos de Gemini en encuestas: {str(e)}")

        # Definir orden de preferencia de modelos, priorizando los que realmente existen y tienen cuota activa (flash-lite)
        preferred_order = [
            'gemini-flash-lite-latest', 'gemini-flash-latest', 'gemini-2.5-flash-lite', 
            'gemini-2.5-flash', 'gemini-3.1-flash-lite', 'gemini-pro-latest', 'gemini-3.5-flash'
        ]
        
        models_to_try = [m for m in preferred_order if m in available_models]
        if not models_to_try and available_models:
            models_to_try = available_models[:3]
        elif not models_to_try:
            models_to_try = preferred_order
            
        active_model_name = models_to_try[0]
        _logger.info(f"Evaluando encuesta con modelo activo: {active_model_name}")
        model = genai.GenerativeModel(active_model_name)
        
        # Tomar TODAS las preguntas abiertas (tengan o no max_score configurado en la encuesta)
        lines_to_evaluate = user_input.user_input_line_ids.filtered(
            lambda l: l.question_id.question_type in ('text_box', 'char_box')
        )
        
        if not lines_to_evaluate:
            applicant._procesar_resultado_encuesta(user_input)
            return
        
        puntos_ia_obtenidos = 0.0
        puntos_ia_maximos = 0.0
        
        for line in lines_to_evaluate:
            pregunta = line.question_id.title
            respuesta = line.value_text_box or line.value_char_box or ""
            max_score = line.question_id.x_ia_max_score if line.question_id.x_ia_max_score > 0 else 10.0
            criterio = line.question_id.x_ia_expected_answer or "Respuesta correcta, coherente y técnica a la pregunta planteada."
            
            puntos_ia_maximos += max_score
            
            if respuesta.strip():
                prompt = f"""
                Actúa como un evaluador técnico experto.
                Pregunta realizada al candidato: "{pregunta}"
                Criterio de Evaluación esperado: "{criterio}"
                
                Respuesta del candidato: "{respuesta}"
                
                Evalúa la respuesta del candidato basándote estrictamente en el criterio y asígnale una nota de 0 a {max_score}.
                Debes responder ESTRICTAMENTE en formato JSON con la siguiente estructura (sin texto markdown adicional ni etiquetas ```json):
                {{
                    "puntos": <numero_flotante>,
                    "justificacion": "<breve justificación>"
                }}
                IMPORTANTE: Asegúrate de escapar correctamente cualquier comilla doble interna en la justificación para que sea un JSON válido.
                """
                
                puntos = 0.0
                justificacion = ""
                exito = False
                
                # Intentar hasta 3 veces si hay error 429 (Rate Limit de cuota gratuita)
                for intento in range(3):
                    try:
                        response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
                        clean_text = response.text.replace("```json", "").replace("```", "").strip()
                        try:
                            result = json.loads(clean_text)
                            puntos = float(result.get('puntos', 0.0))
                            justificacion = result.get('justificacion', '')
                        except Exception:
                            # Fallback robusto con Regex por si Gemini devolvió comillas sin escapar en el código JS
                            import re
                            puntos_match = re.search(r'"puntos"\s*:\s*([\d\.]+)', clean_text)
                            puntos = float(puntos_match.group(1)) if puntos_match else 0.0
                            just_match = re.search(r'"justificacion"\s*:\s*"([^"]+)"', clean_text)
                            justificacion = just_match.group(1) if just_match else clean_text

                        puntos = min(max(puntos, 0.0), max_score)
                        exito = True
                        break # Éxito, salir del bucle
                    except Exception as e:
                        if '429' in str(e) or 'quota' in str(e).lower():
                            _logger.warning(f"Gemini Rate Limit 429 detectado (intento {intento+1}/3). Esperando 15 segundos...")
                            import time
                            time.sleep(15)
                        else:
                            _logger.error(f"Error evaluando con Gemini: {str(e)}")
                            applicant.message_post(body=f"<b style='color:red;'>Error Gemini evaluando '{pregunta}':</b> {str(e)}")
                            break
                            
                if not exito:
                    # Fallback de emergencia: Permitir calificación manual por parte del seleccionador
                    puntos = 0.0
                    justificacion = "⚠️ PENDIENTE DE CALIFICACIÓN MANUAL: La IA no pudo evaluar esta respuesta por límite de cuota (429). Por favor, lea la respuesta del candidato y asigne el puntaje manualmente."
                    applicant.message_post(body=f"<b style='color:red;'>🚨 Alerta de Encuesta ('{pregunta}'):</b> La evaluación automática por IA falló por cuota (429). El examen está pendiente de revisión y calificación manual por parte del seleccionador.")

                # Crear registro detallado para la nueva pestaña (notebook) en el candidato (sin manchar el chatter)
                self.env['hr.applicant.ia.answer'].sudo().create({
                    'applicant_id': applicant.id,
                    'user_input_line_id': line.id,
                    'question': pregunta,
                    'answer': respuesta,
                    'expected_criteria': criterio,
                    'score': puntos,
                    'max_score': max_score,
                    'justification': justificacion
                })
                
                line.sudo().write({'answer_score': puntos})
                puntos_ia_obtenidos += puntos
            else:
                self.env['hr.applicant.ia.answer'].sudo().create({
                    'applicant_id': applicant.id,
                    'user_input_line_id': line.id,
                    'question': pregunta,
                    'answer': "(En blanco)",
                    'expected_criteria': criterio,
                    'score': 0.0,
                    'max_score': max_score,
                    'justification': "El candidato no proporcionó ninguna respuesta."
                })
                    
        # Calcular puntaje final (Puntos Odoo Nativos + Puntos IA)
        native_score = sum(user_input.user_input_line_ids.mapped('answer_score'))
        native_percentage = user_input.scoring_percentage or 0.0
        
        if native_percentage > 0 and native_score > 0:
            native_max = (native_score * 100) / native_percentage
        else:
            native_max = 0.0
            for q in user_input.survey_id.question_ids:
                if q.is_scored_question:
                    if q.question_type == 'simple_choice':
                        max_q = max(q.suggested_answer_ids.mapped('answer_score') or [0.0])
                        native_max += max_q if max_q > 0 else 0
                    elif q.question_type == 'multiple_choice':
                        native_max += sum([s for s in q.suggested_answer_ids.mapped('answer_score') if s > 0])
                    elif q.question_type in ('numerical_box', 'date', 'datetime'):
                        native_max += q.answer_score

        total_obtenido = native_score + puntos_ia_obtenidos
        total_maximo = native_max + puntos_ia_maximos
        
        if total_maximo > 0:
            final_percentage = (total_obtenido / total_maximo) * 100
        else:
            final_percentage = native_percentage
            
        final_percentage = min(final_percentage, 100.0)
        
        # Enviar al flujo
        applicant._procesar_resultado_encuesta(user_input, score=final_percentage)

    def _recalculate_combined_score(self, applicant):
        self = self.sudo()
        applicant = applicant.sudo()
        
        # Recalcular puntos IA obtenidos y máximos desde las respuestas guardadas en hr.applicant.ia.answer
        ia_answers = applicant.x_ia_answer_ids
        puntos_ia_obtenidos = sum(ia_answers.mapped('score'))
        puntos_ia_maximos = sum(ia_answers.mapped('max_score'))
        
        # Puntos nativos (opción múltiple/cerradas)
        closed_lines = self.user_input_line_ids.filtered(lambda l: l.question_id.question_type not in ('text_box', 'char_box'))
        native_score = sum(closed_lines.mapped('answer_score'))
        
        native_max = 0.0
        for q in self.survey_id.question_ids:
            if q.is_scored_question and q.question_type not in ('text_box', 'char_box'):
                if q.question_type == 'simple_choice':
                    max_q = max(q.suggested_answer_ids.mapped('answer_score') or [0.0])
                    native_max += max_q if max_q > 0 else 0
                elif q.question_type == 'multiple_choice':
                    native_max += sum([s for s in q.suggested_answer_ids.mapped('answer_score') if s > 0])
                elif q.question_type in ('numerical_box', 'date', 'datetime'):
                    native_max += q.answer_score

        total_obtenido = native_score + puntos_ia_obtenidos
        total_maximo = native_max + puntos_ia_maximos
        
        if total_maximo > 0:
            final_percentage = (total_obtenido / total_maximo) * 100
        else:
            final_percentage = self.scoring_percentage or 0.0
            
        final_percentage = min(final_percentage, 100.0)
        
        # Si el candidato estaba inactivo (rechazado) pero el seleccionador le subió la nota manualmente para aprobarlo:
        passing_score = self.survey_id.scoring_success_min if self.survey_id.scoring_type != 'no_scoring' else 70.0
        if final_percentage >= passing_score and not applicant.active:
            # Reabrir al candidato y devolverlo a la etapa activa
            applicant.write({'active': True})
            applicant.message_post(body=f"<b style='color:green;'>⭐ Aprobación por Calificación Manual:</b> El seleccionador actualizó la nota a {final_percentage:.1f}% (Mínimo {passing_score}%). Candidato reactivado con éxito.")
            
        applicant._procesar_resultado_encuesta(self, score=final_percentage)
