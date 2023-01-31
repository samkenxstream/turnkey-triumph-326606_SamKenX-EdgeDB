#
# This source file is part of the EdgeDB open source project.
#
# Copyright 2008-present MagicStack Inc. and the EdgeDB authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


from __future__ import annotations
from typing import *

from edb import errors

from edb.edgeql import ast as qlast
from edb.edgeql import compiler as qlcompiler
from edb.edgeql import qltypes

from . import annos as s_anno
from . import delta as sd
from . import expr as s_expr
from . import name as sn
from . import objects as so
from . import referencing
from . import schema as s_schema
from . import sources as s_sources
from . import types as s_types

if TYPE_CHECKING:
    from . import objtypes as s_objtypes


class Trigger(
    referencing.ReferencedInheritingObject,
    s_anno.AnnotationSubject,
    qlkind=qltypes.SchemaObjectClass.TRIGGER,
    data_safe=True,
):

    # XXX: compcoef is zero since we don't have syntax yet
    timing = so.SchemaField(
        qltypes.TriggerTiming,
        coerce=True,
        compcoef=0.0,
        special_ddl_syntax=True,
    )

    kinds = so.SchemaField(
        so.MultiPropSet[qltypes.TriggerKind],
        coerce=True,
        compcoef=0.0,
        special_ddl_syntax=True,
    )

    scope = so.SchemaField(
        qltypes.TriggerScope,
        coerce=True,
        compcoef=0.0,
        special_ddl_syntax=True,
    )

    expr = so.SchemaField(
        s_expr.Expression,
        default=None,
        compcoef=0.909,
        special_ddl_syntax=True,
    )

    subject = so.SchemaField(
        so.InheritingObject,
        compcoef=None,
        inheritable=False)

    @classmethod
    def get_displayname_static(cls, name: sn.Name) -> str:
        sn = cls.get_shortname_static(name)
        if sn.module == '__':
            return sn.name
        else:
            return str(sn)

    def get_derived_name_base(
        self,
        schema: s_schema.Schema,
    ) -> sn.QualName:
        shortname = self.get_shortname(schema)
        return sn.QualName(module='__', name=shortname.name)

    def get_subject(self, schema: s_schema.Schema) -> s_objtypes.ObjectType:
        subj: s_objtypes.ObjectType = self.get_field_value(schema, 'subject')
        return subj


class TriggerCommandContext(
    sd.ObjectCommandContext[Trigger],
    s_anno.AnnotationSubjectCommandContext,
):
    pass


class TriggerSourceCommandContext(
        s_sources.SourceCommandContext[s_sources.Source_T]):
    pass


class TriggerCommand(
    referencing.ReferencedInheritingObjectCommand[Trigger],
    s_anno.AnnotationSubjectCommand[Trigger],
    context_class=TriggerCommandContext,
    referrer_context_class=TriggerSourceCommandContext,
):
    def canonicalize_attributes(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
    ) -> s_schema.Schema:
        schema = super().canonicalize_attributes(schema, context)

        for field in ('expr',):
            if (expr := self.get_local_attribute_value(field)) is None:
                continue

            self.compile_expr_field(
                schema, context,
                field=Trigger.get_field(field),
                value=expr,
            )

        return schema

    def _get_scope(
        self,
        schema: s_schema.Schema,
    ) -> qltypes.TriggerScope:
        return self.get_attribute_value('scope') or self.scls.get_scope(schema)

    def _get_kinds(
        self,
        schema: s_schema.Schema,
    ) -> AbstractSet[qltypes.TriggerKind]:
        return self.get_attribute_value('kinds') or self.scls.get_kinds(schema)

    def compile_expr_field(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
        field: so.Field[Any],
        value: s_expr.Expression,
        track_schema_ref_exprs: bool=False,
    ) -> s_expr.CompiledExpression:
        if field.name == 'expr':
            from edb.ir import pathid

            parent_ctx = self.get_referrer_context_or_die(context)
            source = parent_ctx.op.get_object(schema, context)
            assert isinstance(source, s_types.Type)
            # XXX: in_ddl_context_name is disabled for now because
            # it causes the compiler to reject DML; we might actually
            # want it for something, though, so we might need to
            # improve that restriction.
            # parent_vname = source.get_verbosename(schema)
            # pol_name = self.get_verbosename(parent=parent_vname)
            # in_ddl_context_name = pol_name

            scope = self._get_scope(schema)
            kinds = self._get_kinds(schema)

            anchors = {}
            if qltypes.TriggerKind.Insert not in kinds:
                anchors['__old__'] = pathid.PathId.from_type(
                    schema, source, typename=sn.QualName(
                        module='__derived__', name='__old__')
                )
            if qltypes.TriggerKind.Delete not in kinds:
                anchors['__new__'] = pathid.PathId.from_type(
                    schema, source, typename=sn.QualName(
                        module='__derived__', name='__new__')
                )

            singletons = (
                frozenset(anchors.values())
                if scope == qltypes.TriggerScope.Each else frozenset()
            )

            assert isinstance(source, s_types.Type)

            return type(value).compiled(
                value,
                schema=schema,
                options=qlcompiler.CompilerOptions(
                    modaliases=context.modaliases,
                    schema_object_context=self.get_schema_metaclass(),
                    anchors=anchors,
                    singletons=singletons,
                    apply_query_rewrites=not context.stdmode,
                    track_schema_ref_exprs=track_schema_ref_exprs,
                    # in_ddl_context_name=in_ddl_context_name,
                    detached=True,
                ),
            )
        else:
            return super().compile_expr_field(
                schema, context, field, value, track_schema_ref_exprs)

    def get_dummy_expr_field_value(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
        field: so.Field[Any],
        value: Any,
    ) -> Optional[s_expr.Expression]:
        if field.name == 'expr':
            return s_expr.Expression(text='false')
        else:
            raise NotImplementedError(f'unhandled field {field.name!r}')

    # XXX: This is duplicated with AccessPolicy (and other places?)
    # XXX: Name can collide with access policies, other things? Is that right?
    @classmethod
    def _classname_from_ast(
        cls,
        schema: s_schema.Schema,
        astnode: qlast.NamedDDL,
        context: sd.CommandContext,
    ) -> sn.QualName:
        referrer_ctx = cls.get_referrer_context(context)
        if referrer_ctx is not None:

            referrer_name = context.get_referrer_name(referrer_ctx)

            shortname = sn.QualName(module='__', name=astnode.name.name)

            name = sn.QualName(
                module=referrer_name.module,
                name=sn.get_specialized_name(shortname, str(referrer_name)),
            )
        else:
            name = super()._classname_from_ast(schema, astnode, context)

        return name

    def _deparse_name(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
        name: sn.Name,
    ) -> qlast.ObjectRef:

        ref = super()._deparse_name(schema, context, name)
        ref.module = ''
        return ref

    def validate_object(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
    ) -> None:
        # XXX: verify we don't have the same bug as access policies
        # where linkprop defaults are broken.
        # (I think we won't need to, since we'll operate after
        # the *real* operations)
        pass


class CreateTrigger(
    TriggerCommand,
    referencing.CreateReferencedInheritingObject[Trigger],
):
    referenced_astnode = astnode = qlast.CreateTrigger

    def get_ast_attr_for_field(
        self,
        field: str,
        astnode: Type[qlast.DDLOperation],
    ) -> Optional[str]:
        if (
            field in ('timing', 'kinds', 'scope', 'expr')
            and issubclass(astnode, qlast.CreateTrigger)
        ):
            return field
        else:
            return super().get_ast_attr_for_field(field, astnode)

    @classmethod
    def _cmd_tree_from_ast(
        cls,
        schema: s_schema.Schema,
        astnode: qlast.DDLOperation,
        context: sd.CommandContext,
    ) -> sd.Command:
        cmd = super()._cmd_tree_from_ast(schema, astnode, context)

        assert isinstance(astnode, qlast.CreateTrigger)
        assert isinstance(cmd, TriggerCommand)

        if astnode.expr:
            cmd.set_attribute_value(
                'expr',
                s_expr.Expression.from_ast(
                    astnode.expr, schema, context.modaliases,
                    context.localnames,
                ),
                source_context=astnode.expr.context,
            )

        cmd.set_attribute_value('timing', astnode.timing)
        cmd.set_attribute_value('kinds', astnode.kinds)
        cmd.set_attribute_value('scope', astnode.scope)

        return cmd


class RenameTrigger(
    TriggerCommand,
    referencing.RenameReferencedInheritingObject[Trigger],
):
    pass


class RebaseTrigger(
    TriggerCommand,
    referencing.RebaseReferencedInheritingObject[Trigger],
):
    pass


class AlterTrigger(
    TriggerCommand,
    referencing.AlterReferencedInheritingObject[Trigger],
):
    referenced_astnode = astnode = qlast.AlterTrigger

    def _alter_begin(
        self,
        schema: s_schema.Schema,
        context: sd.CommandContext,
    ) -> s_schema.Schema:
        schema = super()._alter_begin(schema, context)

        # TODO: We may wish to support this in the future but it will
        # take some thought.
        if (
            self.get_attribute_value('owned')
            and not self.get_orig_attribute_value('owned')
        ):
            raise errors.SchemaDefinitionError(
                f'cannot alter the definition of inherited trigger '
                f'{self.scls.get_displayname(schema)}',
                context=self.source_context
            )

        return schema


class DeleteTrigger(
    TriggerCommand,
    referencing.DeleteReferencedInheritingObject[Trigger],
):
    referenced_astnode = astnode = qlast.DropTrigger