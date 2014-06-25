# -*- coding: utf-8 -*-
##############################################################################
#
#    Author: Nicolas Bessi. Copyright Camptocamp SA
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
import re

from openerp.osv.orm import Model, fields
from openerp.tools import mod10r


class AccountMoveLine(Model):

    _inherit = "account.move.line"

    _compile_get_ref = re.compile('[^0-9]')

    _columns = {
        'transaction_ref': fields.char('Transaction Ref.', size=128),
    }

    def _get_bvr_amount(self, cr, uid,  move, rtype=None):
        """Hook to get amount in CHF for BVR"""
        return move.debit

    def get_bvr_ref(self, cursor, uid, move_line_id, context=None):
        """Retrieve ESR/BVR reference from move line in order to print it

        Returns False when no BVR reference should be generated.  No
        reference is generated when a transaction reference already
        exists for the line (likely been generated by a payment service).
        """
        res = ''
        if isinstance(move_line_id, (tuple, list)):
            assert len(move_line_id) == 1, "Only 1 ID expected"
            move_line_id = move_line_id[0]

        move_line = self.browse(cursor, uid, move_line_id, context=context)
        # We check if the type is bvr, if not we return false
        if move_line.invoice.partner_bank_id.state != 'bvr':
            return ''
        if move_line.invoice.partner_bank_id.bvr_adherent_num:
            res = move_line.invoice.partner_bank_id.bvr_adherent_num
        move_number = ''

        if move_line.invoice.number:
            compound = str(move_line.invoice.number) + str(move_line_id)
            move_number = self._compile_get_ref.sub('', compound)
        reference = mod10r(res + move_number.rjust(26 - len(res), '0'))
        if (move_line.transaction_ref and
                move_line.transaction_ref != reference):
            # the line has already a transaction id and it is not
            # a BVR reference
            return ''
        return reference


class AccountInvoice(Model):
    """Inherit account.invoice in order to add bvr
    printing functionnalites. BVR is a Swiss payment vector"""
    _inherit = "account.invoice"

    _compile_get_ref = re.compile('[^0-9]')

    def _get_reference_type(self, cursor, user, context=None):
        """Function used by the function field 'reference_type'
        in order to initalise available BVR Reference Types
        """
        res = super(AccountInvoice, self)._get_reference_type(cursor, user,
                                                              context=context)
        res.append(('bvr', 'BVR'))
        return res

    def _compute_full_bvr_name(self, cursor, uid, ids, field_names, arg, context=None):
        res = {}
        move_line_obj = self.pool.get('account.move.line')
        account_obj = self.pool.get('account.account')
        tier_account_id = account_obj.search(cursor, uid,
                                             [('type', 'in', ['receivable', 'payable'])])
        for inv in self.browse(cursor, uid, ids, context=context):
            move_lines = move_line_obj.search(cursor, uid,
                                              [('move_id', '=', inv.move_id.id),
                                               ('account_id', 'in', tier_account_id)])
            if move_lines:
                refs = []
                for move_line in move_line_obj.browse(cursor, uid, move_lines, context=context):
                    refs.append(AccountInvoice._space(move_line.get_bvr_ref()))
                res[inv.id] = ' ; '.join(refs)
        return res

    _columns = {
        ### BVR reference type BVR or FREE
        'reference_type': fields.selection(_get_reference_type,
                                           'Reference Type',
                                           required=True),

        ### Partner bank link between bank and partner id
        'partner_bank_id': fields.many2one('res.partner.bank',
                                           'Bank Account',
                                           help='The partner bank account to pay\n'
                                                'Keep empty to use the default'),

        'bvr_reference': fields.function(_compute_full_bvr_name,
                                         type="char",
                                         size=512,
                                         string="BVR REF.",
                                         store=True,
                                         readonly=True)
    }

    @staticmethod
    def _space(nbr, nbrspc=5):
        """Spaces * 5.

        Example:
            AccountInvoice._space('123456789012345')
            '12 34567 89012 345'
        """
        return ''.join([' '[(i - 2) % nbrspc:] + c for i, c in enumerate(nbr)])

    def _update_ref_on_account_analytic_line(self, cr, uid, ref, move_id, context=None):
        """Propagate reference on analytic line"""
        cr.execute('UPDATE account_analytic_line SET ref=%s'
                   '   FROM account_move_line '
                   ' WHERE account_move_line.move_id = %s '
                   '   AND account_analytic_line.move_id = account_move_line.id',
                   (ref, move_id))
        return True

    def _action_bvr_number_move_line(self, cr, uid, invoice, move_line,
                                     ref, context=None):
        """Propagate reference on move lines and analytic lines"""
        if not ref:
            return
        cr.execute('UPDATE account_move_line SET transaction_ref=%s'
                   '  WHERE id=%s', (ref, move_line.id))
        self._update_ref_on_account_analytic_line(cr, uid, ref,
                                                  move_line.move_id.id)

    def action_number(self, cr, uid, ids, context=None):
        """ Copy the BVR/ESR reference in the transaction_ref of move lines.

        For customers invoices: the BVR reference is computed using
        ``get_bvr_ref()`` on the invoice or move lines.

        For suppliers invoices: the BVR reference is stored in the reference
        field of the invoice.

        """
        res = super(AccountInvoice, self).action_number(cr, uid, ids, context=context)
        move_line_obj = self.pool.get('account.move.line')

        for inv in self.browse(cr, uid, ids, context=context):
            move_line_ids = move_line_obj.search(
                cr, uid,
                [('move_id', '=', inv.move_id.id),
                 ('account_id', '=', inv.account_id.id)],
                context=context
            )
            if not move_line_ids:
                continue
            move_lines = move_line_obj.browse(cr, uid, move_line_ids,
                                              context=context)
            for move_line in move_lines:
                if inv.type in ('out_invoice', 'out_refund'):
                    ref = move_line.get_bvr_ref()
                elif inv.reference_type == 'bvr' and inv.reference:
                    ref = inv.reference
                else:
                    ref = False
                self._action_bvr_number_move_line(cr, uid, inv,
                                                  move_line, ref,
                                                  context=context)
        return res

    def copy(self, cursor, uid, inv_id, default=None, context=None):
        default = default or {}
        default.update({'reference': False})
        return super(AccountInvoice, self).copy(cursor, uid, inv_id, default, context)


class AccountTaxCode(Model):
    """Inherit account tax code in order
    to add a Case code"""
    _name = 'account.tax.code'
    _inherit = "account.tax.code"
    _columns = {
        'code': fields.char('Case Code', size=512),
    }
