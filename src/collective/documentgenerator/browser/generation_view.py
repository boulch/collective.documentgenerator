# -*- coding: utf-8 -*-

from AccessControl import Unauthorized

from Products.Five import BrowserView

from StringIO import StringIO

from collective.documentgenerator import config
from collective.documentgenerator.content.pod_template import IPODTemplate
from collective.documentgenerator.interfaces import IDocumentFactory
from collective.documentgenerator.interfaces import PODTemplateNotFoundError

from imio.helpers.security import call_as_super_user
from imio.pyutils.system import get_temporary_filename

from plone import api

from zope.component import queryMultiAdapter

import appy.pod.renderer
import mimetypes
import os


class DocumentGenerationView(BrowserView):
    """
    Document generation with appy.
    """
    def __init__(self, context, request):
        self.context = context
        self.request = request

    def __call__(self):
        return self.generate_and_download_doc()

    def generate_and_download_doc(self):
        doc, doc_name = self.generate_doc()
        self.set_header_response(doc_name)
        return doc

    def generate_doc(self):
        """
        Generate a document and returns it as a downloadable file.
        """
        # The user calling the generation action is not always allowed to access
        # the PODtemplates, so we use call_as_super_user to be sure to find
        # them..
        pod_template = call_as_super_user(self.get_pod_template)

        if not pod_template.can_be_generated(self.context):
            raise Unauthorized('You are not allowed to generate this document.')

        document_path = self.recursive_generate_doc(pod_template)

        rendered_document = open(document_path, 'rb')
        rendered = rendered_document.read()
        rendered_document.close()
        os.remove(document_path)

        filename = u'{}.{}'.format(pod_template.title, self.get_generation_format())

        return rendered, filename

    def recursive_generate_doc(self, pod_template):

        sub_templates = pod_template.get_templates_to_merge()
        sub_documents = {}
        for context_name, sub_pod in sub_templates.iteritems():
            sub_documents[context_name] = self.recursive_generate_doc(sub_pod)

        document_template = pod_template.get_file()
        file_type = self.get_generation_format()

        document_path = self.render_document(document_template, file_type, sub_documents)

        return document_path

    def get_pod_template(self):
        template_uid = self.get_pod_template_uid()
        catalog = api.portal.get_tool('portal_catalog')

        template_brains = catalog(object_provides=IPODTemplate.__identifier__, UID=template_uid)
        if not template_brains:
            raise PODTemplateNotFoundError(
                "Couldn't find POD template with UID '{}'".format(template_uid)
            )

        pod_template = template_brains[0].getObject()
        return pod_template

    def get_pod_template_uid(self):
        template_uid = self.request.get('doc_uid', None)
        return template_uid

    def get_generation_format(self):
        generation_format = self.request.get('output_format', 'odt')
        return generation_format

    def render_document(self, document_obj, file_type, sub_documents):
        filename = document_obj.filename or '.odt'
        temp_filename = get_temporary_filename(filename)
        # Prepare rendering context
        helper_view = self.get_generation_context_helper()

        generation_context = {
            'context': getattr(helper_view, 'context', None),
            'view': helper_view
        }
        generation_context.update(sub_documents)

        renderer = appy.pod.renderer.Renderer(
            StringIO(document_obj.data),
            generation_context,
            temp_filename,
            pythonWithUnoPath=config.get_uno_path(),
        )

        # it is only now that we can initialize helper view's appy pod renderer
        helper_view._set_appy_renderer(renderer)

        renderer.run()

        return temp_filename

    def get_generation_context_helper(self):
        helper = self.context.unrestrictedTraverse('@@document_generation_helper_view')
        return helper

    def set_header_response(self, filename):
        # Tell the browser that the resulting page contains ODT
        response = self.request.RESPONSE
        mimetype = mimetypes.guess_type(filename)[0]
        response.setHeader('Content-type', mimetype)
        response.setHeader(
            'Content-disposition',
            u'inline;filename="{}"'.format(filename).encode('utf-8')
        )


class PersistentDocumentGenerationView(DocumentGenerationView):
    """
    """

    def __call__(self):
        self.generate_persistant_doc()

    def generate_persistant_doc(self):
        """
        Generate a document, create a 'File' on the context with the generated document
        and redirect to the created File.
        """

        doc, doc_name = self.generate_doc()

        title, extension = doc_name.split('.')

        factory = queryMultiAdapter((self.context, self.request), IDocumentFactory)

        #  Bypass any File creation permission of the user. If the user isnt
        #  supposed to save generated document on the site, then its the permission
        #  to call the generation view that should be changed.
        call_as_super_user(
            factory.create,
            doc_file=doc,
            title=title,
            extension=extension
        )
