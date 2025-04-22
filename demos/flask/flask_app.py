#!/usr/bin/env python3

import logging
import os # Import os module to generate a secret key
from fhirclient import client
from fhirclient.models.medication import Medication
from fhirclient.models.medicationrequest import MedicationRequest

from flask import Flask, request, redirect, session

# app setup
smart_defaults = {
    'app_id': 'my-flask-app',
    'api_base': 'https://launch.smarthealthit.org/v/r4/fhir',
    'redirect_uri': 'https://eircare-preview-xyx5dpyhxq-uc.a.run.app/fhir-app/',
    'scope': 'openid profile offline_access email system/AllergyIntolerance.read system/Patient.read system/Provenance.read system/CarePlan.read system/Organization.read system/Condition.read system/Encounter.read system/DiagnosticReport.read system/CareTeam.read system/Observation.read system/Practitioner.read system/Goal.read system/Immunization.read system/Medication.read'
}

app = Flask(__name__)

# ** Set the secret key early **
# In a real app, load this from environment variable or secure config management
app.secret_key = os.urandom(24) 

def _save_state(state):
    session['state'] = state

def _get_smart():
    state = session.get('state')
    if state:
        return client.FHIRClient(state=state, save_func=_save_state)
    elif smart_defaults.get('api_base'): # Use .get() for safer access
         # Pass the app's secret_key to the FHIRClient settings (ensure it's available)
        settings = smart_defaults.copy()  # Create a copy to avoid modifying the original
        settings['secret_key'] = app.secret_key # If needed, make it available in settings
        return client.FHIRClient(settings=settings, save_func=_save_state)
    else:
        logging.error("Cannot initialize FHIRClient: api_base not set in smart_defaults.")
        return None

def _logout():
    if 'state' in session:
        smart = _get_smart()
        if smart:  # Ensure smart client was initialized
            smart.reset_patient()
        # Also clear the flask session
        session.pop('state', None)

def _reset():
    # Clear the flask session explicitly
    session.pop('state', None)

def _get_prescriptions(smart):
    if not smart or not smart.patient_id:
        logging.warning("Cannot get prescriptions: FHIR client not ready or no patient selected.")
        return [] # Return empty list if smart client not ready or no patient
    try:
        search = MedicationRequest.where({'patient': smart.patient_id})
        logging.info(f"Searching for prescriptions for patient {smart.patient_id}")
        # Use perform_resources() for potentially simpler handling if iter not needed
        resources = search.perform_resources(smart.server)
        logging.info(f"Found {len(resources)} prescription resources.")
        return resources
    except Exception as e:
        logging.exception("Failed to get prescriptions")
        return [] # Return empty on error

def _get_medication_by_ref(ref, smart):
    if not ref or '/' not in ref:
        logging.error(f"Invalid medication reference format: {ref}")
        return None
    try:
        med_id = ref.split("/")[1]
        # Ensure server object is valid
        if smart and smart.server:
            logging.info(f"Reading Medication/{med_id}")
            med_resource = Medication.read(med_id, smart.server)
            return med_resource.code # Return the codeableConcept
        else:
            logging.error("FHIR client server unavailable for medication lookup")
            return None
    except Exception as e:
        logging.exception(f"Failed to read Medication/{med_id}")
        return None

def _med_name(med_code):
    if not med_code: # Check if med_code (CodeableConcept) is None or empty
        return "Medication data unavailable"
        
    # Prefer coding display text if available
    if med_code.coding:
        # Prioritize RxNorm display name
        rxnorm_name = next((coding.display for coding in med_code.coding if coding.system == 'http://www.nlm.nih.gov/research/umls/rxnorm' and coding.display), None)
        if rxnorm_name:
            return rxnorm_name
        # Fallback to any other display name
        any_display_name = next((coding.display for coding in med_code.coding if coding.display), None)
        if any_display_name:
            return any_display_name
             
    # If no coding display name found, use the CodeableConcept's text if available
    if med_code.text:
        return med_code.text
        
    return "Unnamed Medication"

def _get_med_name(prescription, smart_client=None):
    if not prescription:
        return "Invalid prescription data"
        
    med_code = None
    # Check medicationCodeableConcept first
    if hasattr(prescription, 'medicationCodeableConcept') and prescription.medicationCodeableConcept:
        med_code = prescription.medicationCodeableConcept
        # If codeable concept has text but no coding, use that text directly
        if not med_code.coding and med_code.text:
             return med_code.text
             
    # If not found or empty, check medicationReference
    elif hasattr(prescription, 'medicationReference') and prescription.medicationReference and prescription.medicationReference.reference and smart_client:
        logging.debug(f"Fetching medication reference: {prescription.medicationReference.reference}")
        med_code = _get_medication_by_ref(prescription.medicationReference.reference, smart_client)
    
    # If we found a codeable concept from either source, format its name
    if med_code:
        return _med_name(med_code)
    else:
        # Final fallback if no medication info could be resolved
        logging.warning(f"Could not resolve medication name for prescription {prescription.id if prescription.id else 'unknown'}")
        return 'Error: medication details not found'

# views

@app.route('/')
@app.route('/index.html')
def index():
    """ The app's main page.
    """
    try:
        smart = _get_smart()
        body = "<h1>Hello</h1>"

        if smart is None:
            body += """<p>Error: FHIRClient could not be initialized. Check server logs.</p>"""

        elif smart.ready and smart.patient:
            # Ensure patient resource is fetched if only patient_id is available
            if smart.patient.name is None:
                try:
                    smart.prepare()
                except Exception as e:
                    logging.exception("Failed to fetch patient details after initial auth")
                    # Render page anyway, but show patient is missing details
                    
            patient_name = 'Unknown Patient'
            if smart.patient and smart.patient.name:
                patient_name = smart.human_name(smart.patient.name[0])
            else:
                 logging.warning(f"Patient resource available (ID: {smart.patient_id}) but lacks name details.")

            body += f"<p>Authorized for <em>{patient_name}</em> (ID: {smart.patient_id}).</p>"
            
            pres = _get_prescriptions(smart)
            if pres:
                med_names = [_get_med_name(p, smart_client=smart) for p in pres]
                if med_names:
                    body += f"<p>Prescriptions: <ul><li>{'</li><li>'.join(med_names)}</li></ul></p>"
                else:
                    body += "<p>Found prescription resources, but could not extract medication names.</p>"
            else:
                body += "<p>(No prescriptions found or error fetching them for this patient)</p>"
            body += """<p><a href="/logout">Change patient / Logout</a></p>"""

        else:
            # If not ready, generate authorization link
            auth_url = smart.authorize_url
            if auth_url:
                body += f'<p>App configured for AthenaHealth Preview. Please <a href="{auth_url}">authorize</a>.</p>'
            else:
                body += """<p>Error: Could not get authorization URL. Check FHIRClient setup and logs.</p>"""
            body += """<p><a href="/reset" style="font-size:small;">Reset Session</a></p>"""
            
    except Exception as e:
        logging.exception("Error rendering index page")
        body = "<h1>Application Error</h1><p>An error occurred: {}. Check server logs.</p>".format(e)
        body += """<p><a href="/reset">Reset Session</a></p>"""
        
    return body

@app.route('/fhir-app/')
def callback():
    """ OAuth2 callback interception.
    """
    smart = _get_smart()
    if not smart:
         return "<h1>Error</h1><p>FHIR Client not initialized. Cannot handle callback.</p>"
    try:
        logging.debug(f"Handling callback: {request.url}")

        # ** 1. Extract code and state from request parameters **
        auth_code = request.args.get('code')
        state = request.args.get('state')

        if not auth_code:
            logging.error("Authorization code missing from callback request.")
            return "<h1>Authorization Error</h1><p>Authorization code is missing.</p>"

        # ** 2. [INCOMPLETE] Make a request to the AthenaHealth token endpoint **
        # [I CANNOT PERFORM THIS STEP]

        # ** 3. [PLACEHOLDER] Handle the token response **
        # The fhirclient library expects to handle the code exchange
        # and set state internally, but I need to explicitly pass in the code.
        # This next line is a placeholder and needs to be replaced with actual
        # token handling logic
        smart.handle_callback(request.url)

        logging.info(f"Callback successful (Placeholder). Ready state: {smart.ready}, Patient ID: {smart.patient_id}")

    except Exception as e:
        logging.exception("Callback handler failed")
        return """<h1>Authorization Error</h1><p>{0}</p><p><a href="/">Start over</a></p>""".format(e)
    return redirect('/')


@app.route('/logout')
def logout():
    _logout()
    return redirect('/')


@app.route('/reset')
def reset():
    _reset()
    return redirect('/')


# start the app
if __name__ == '__main__':
    import flaskbeaker
    
    # Setup Flask-Beaker *after* setting the secret key
    flaskbeaker.FlaskBeaker.setup_app(app)
    
    logging.basicConfig(level=logging.DEBUG)
    # Set debug=False for production deployments
    # Use PORT environment variable provided by Cloud Run, default to 8080
    port = int(os.environ.get("PORT", 8080))
    # Run on 0.0.0.0 to be accessible externally
    app.run(debug=False, host='0.0.0.0', port=port)
